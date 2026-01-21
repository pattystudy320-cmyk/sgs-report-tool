import streamlit as st
import pdfplumber
import pandas as pd
import re
import os
import json
from openai import OpenAI

# =====================
# 1. 基本設定與 API 初始化
# =====================
# 嘗試從 Secrets 或 環境變數 抓取 Key
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

if not api_key:
    st.error("❌ 未偵測到 API Key！請確認 Streamlit Secrets 設定。")
    st.stop()

client = OpenAI(api_key=api_key)

# 定義顯示順序與欄位
ITEMS_ORDER = [
    "ITEM", "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "F", "Cl", "Br", "I", "PFOS", "PFAS", 
    "DATE", "檔案名稱"
]

# 定義要抓取的化學項目 key (對應 JSON)
CHEMICAL_ITEMS = [
    "Pb", "Cd", "Hg", "CrVI", 
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "Cl", "Br", "I", "PFOS"
]

# 優先級權重：數值(3) > NEGATIVE(2) > REPORT(2, PFAS專用) > N.D.(1) > None(0)
PRIORITY_MAP = {
    "number": 3, 
    "negative": 2, 
    "report": 2,  # PFAS 若有測到視為重要
    "nd": 1, 
    "none": 0
}

# =====================
# 2. PDF 文字擷取 (安全模式)
# =====================
def extract_text(file):
    """
    從 PDF 提取文字，並強制處理編碼問題，避免 ASCII 錯誤。
    """
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            # 限制讀取頁數，避免 Token 爆炸 (通常摘要在前 5 頁)
            for i, p in enumerate(pdf.pages):
                if i > 5: break 
                t = p.extract_text()
                if t:
                    text += t + "\n"
    except Exception:
        return "" 
        
    if not text.strip():
        return ""

    # 強制轉碼為 UTF-8，忽略無法辨識的字元
    return text.encode("utf-8", "ignore").decode("utf-8")

# =====================
# 3. 日期擷取 (Regex 規則)
# =====================
def extract_date(text):
    """
    使用正規表達式抓取報告日期 (格式: YYYY/MM/DD or DD/MM/YYYY)
    """
    patterns = [
        r"\b20\d{2}[-/]\d{2}[-/]\d{2}\b", # 2024-01-01
        r"\b\d{2}[-/]\d{2}[-/]20\d{2}\b"  # 01-01-2024
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group()
    return None

# =====================
# 4. AI 語意解析 (大腦核心)
# =====================
def parse_with_ai(text):
    if not text:
        return {}

    # 擷取前 4000 字元供 AI 判讀
    truncated_text = text[:4000] 
    
    prompt = f"""
你是一位專業的化學檢測報告分析師。請分析以下報告文字，並提取指定的化學物質數值。

=== 提取目標 ===
1. 單一物質: Pb, Cd, Hg, CrVI, DEHP, BBP, DBP, DIBP, F, Cl, Br, I, PFOS
2. 複合項目 (PBBs, PBDEs): 請列出該類別下所有被檢測出的「子項目數值」清單 (例如 DecaBDE, NonBDE 等)，以便後續加總。
3. PFAS: 請檢查 "Test Requested" 或 "測試項目" 中是否提及 PFAS (或 Per- and polyfluoroalkyl substances)。只要有提及，請回傳 true。

=== 數值正規化規則 (非常重要) ===
- 若結果為 "n.d.", "N.D.", "Not Detected", "<RL", "<MDL" -> 數值填 "N.D."
- 若結果為 "Negative", "Inconclusive" -> 數值填 "NEGATIVE"
- 若有具體數值 (例如 "10.5", "5 mg/kg") -> 只回傳數字 "10.5" (不要單位)
- 忽略 MDL, RL, Limit (限值) 等欄位，只要 Test Result (測試結果)。

=== 輸出 JSON 格式 ===
{{
  "items": {{
    "Pb": "N.D.",
    "Cd": "10.5",
    "F": "N.D.",
    ... (其他單一物質)
  }},
  "pbbs_list": ["N.D.", "5", "N.D."], 
  "pbdes_list": ["N.D.", "N.D."],
  "pfas_detected": true
}}

=== 報告內容 ===
{truncated_text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {}

# =====================
# 5. 數據正規化與計算邏輯
# =====================
def normalize_value(val):
    """
    將 AI 回傳的字串轉換為可比對的物件 {type, value}
    """
    if val is None:
        return {"type": "none", "value": None}
    
    v_str = str(val).strip().upper()
    
    # 1. 處理 N.D.
    if v_str in ["N.D.", "ND", "NOT DETECTED"] or v_str.startswith("<"):
        return {"type": "nd", "value": "N.D."}
    
    # 2. 處理 NEGATIVE
    if "NEGATIVE" in v_str:
        return {"type": "negative", "value": "NEGATIVE"}

    # 3. 處理數值 (嘗試轉 float)
    try:
        clean_v = re.sub(r"[^\d\.]", "", v_str) # 移除非數字
        float_v = float(clean_v)
        return {"type": "number", "value": float_v}
    except:
        return {"type": "none", "value": None}

def calculate_sum(value_list):
    """
    計算 PBBs / PBDEs 的總和。
    邏輯: 只要有一個子項目有數值，就加總；如果全都是 N.D.，結果就是 N.D.
    """
    if not value_list:
        return {"type": "nd", "value": "N.D."}
    
    total = 0.0
    has_number = False
    
    for v in value_list:
        norm = normalize_value(v)
        if norm["type"] == "number":
            total += norm["value"]
            has_number = True
    
    if has_number:
        return {"type": "number", "value": total}
    else:
        return {"type": "nd", "value": "N.D."}

# =====================
# 6. 核心比對邏輯 (PK 大小)
# =====================
def compare_and_pick_best(current_best, new_val, file_name):
    """
    比對新舊數值，決定保留哪一個。
    current_best: 目前保留的最佳結果 {value, type, file}
    new_val: 新讀到的結果 {value, type}
    file_name: 新檔案名稱
    """
    # 建立標準格式
    new_obj = {
        "value": new_val["value"],
        "type": new_val["type"],
        "file": file_name
    }

    if current_best is None:
        return new_obj
    
    old_p = PRIORITY_MAP.get(current_best["type"], 0)
    new_p = PRIORITY_MAP.get(new_val["type"], 0)
    
    # 規則 1: 優先級高者勝 (Number > Negative > ND)
    if new_p > old_p:
        return new_obj
    
    # 規則 2: 同為數值，取最大值
    if new_p == old_p and new_val["type"] == "number":
        if new_val["value"] > current_best["value"]:
            return new_obj
            
    # 其他情況保留舊的 (或先來後到，這邊保留舊的即可)
    return current_best

# =====================
# 7. Streamlit 主程式
# =====================
st.set_page_config(page_title="SGS/Intertek 報告彙總系統", layout="wide")
st.title("🧪 第三方檢測報告自動彙總系統")
st.markdown("支援多份 PDF 上傳，自動抓取最大值，並整合 Pb 來源檔案名稱。")

uploaded_files = st.file_uploader("請上傳 PDF 檢測報告", type="pdf", accept_multiple_files=True)

if uploaded_files:
    # 初始化最終結果字典，預設為 None
    final_results = {item: None for item in ITEMS_ORDER}
    
    # 用來暫存 Pb 的最大值來源檔名 (特殊需求)
    pb_max_file = "" 
    
    # 顯示進度條
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    errors = []

    for idx, f in enumerate(uploaded_files):
        # 1. 安全檔名處理
        try:
            safe_filename = f.name.encode("utf-8", "ignore").decode("utf-8")
        except:
            safe_filename = f"file_{idx}.pdf"
            
        status_text.text(f"正在分析 ({idx+1}/{len(uploaded_files)}): {safe_filename} ...")
        
        try:
            # 2. 提取文字
            raw_text = extract_text(f)
            if not raw_text:
                raise ValueError("無法提取文字")

            # 3. 抓取日期 (若還沒抓到過，或這份報告日期比較新? 需求沒說日期邏輯，這裡假設抓第一份有效的)
            date_found = extract_date(raw_text)
            if date_found and final_results.get("DATE") is None:
                final_results["DATE"] = {"value": date_found, "type": "string", "file": safe_filename}

            # 4. AI 解析
            ai_data = parse_with_ai(raw_text)
            
            # --- 處理單一項目 (Pb, Cd...) ---
            items_dict = ai_data.get("items", {})
            for key in CHEMICAL_ITEMS:
                val = items_dict.get(key)
                norm_val = normalize_value(val)
                final_results[key] = compare_and_pick_best(final_results[key], norm_val, safe_filename)

            # --- 處理 PBBs / PBDEs (加總) ---
            pbb_res = calculate_sum(ai_data.get("pbbs_list", []))
            final_results["PBBs"] = compare_and_pick_best(final_results["PBBs"], pbb_res, safe_filename)
            
            pbde_res = calculate_sum(ai_data.get("pbdes_list", []))
            final_results["PBDEs"] = compare_and_pick_best(final_results["PBDEs"], pbde_res, safe_filename)

            # --- 處理 PFAS (特殊邏輯: 有測就是 REPORT) ---
            if ai_data.get("pfas_detected") is True:
                pfas_val = {"type": "report", "value": "REPORT"}
                final_results["PFAS"] = compare_and_pick_best(final_results["PFAS"], pfas_val, safe_filename)
            else:
                # 如果這份沒測到，視為 N.D. 參與比對
                pfas_nd = {"type": "nd", "value": "N.D."}
                final_results["PFAS"] = compare_and_pick_best(final_results["PFAS"], pfas_nd, safe_filename)

        except Exception as e:
            err_msg = str(e).encode("utf-8", "ignore").decode("utf-8")
            errors.append({"檔案": safe_filename, "錯誤": err_msg})
        
        # 更新進度
        progress_bar.progress((idx + 1) / len(uploaded_files))

    # =====================
    # 8. 產出最終報表
    # =====================
    status_text.text("分析完成！正在彙總數據...")
    
    # 準備 DataFrame 的資料列
    display_row = {}
    display_row["ITEM"] = "RESULT" # 第一欄固定文字
    
    # 填入化學數值
    for k in CHEMICAL_ITEMS + ["PBBs", "PBDEs", "PFAS"]:
        res = final_results.get(k)
        if res:
            display_row[k] = res["value"]
        else:
            display_row[k] = "" # 空白代表沒抓到

    # 填入日期
    date_res = final_results.get("DATE")
    display_row["DATE"] = date_res["value"] if date_res else ""

    # 填入檔案名稱 (依據 Pb 的來源)
    pb_res = final_results.get("Pb")
    if pb_res and pb_res.get("file"):
        display_row["檔案名稱"] = pb_res["file"]
    else:
        # 如果沒有 Pb，就抓第一個檔案的名字當代表，或是留空
        display_row["檔案名稱"] = uploaded_files[0].name if uploaded_files else ""

    # 轉成 DataFrame 並排序欄位
    df = pd.DataFrame([display_row])
    
    # 確保欄位順序正確 (依照 ITEM_ORDER)
    # Pandas 可能會缺某些欄位，要補齊
    for col in ITEMS_ORDER:
        if col not in df.columns:
            df[col] = ""
            
    df = df[ITEMS_ORDER] # 強制排序

    # 顯示
    st.dataframe(df, use_container_width=True)

    if errors:
        st.warning("⚠️ 部分檔案解析異常：")
        st.dataframe(pd.DataFrame(errors), use_container_width=True)
    
    progress_bar.empty()
    status_text.empty()
