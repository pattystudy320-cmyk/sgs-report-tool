import streamlit as st
import pdfplumber
import pandas as pd
import re
import os
import json
from openai import OpenAI

# =====================
# 1. 基本設定
# =====================
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

if not api_key:
    st.error("❌ 未偵測到 API Key！請確認 Streamlit Secrets 設定。")
    st.stop()

client = OpenAI(api_key=api_key)

# 顯示順序
ITEMS_ORDER = [
    "ITEM", "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "F", "Cl", "Br", "I", "PFOS", "PFAS", 
    "DATE", "檔案名稱"
]

# AI 需回傳的標準 Key (必須與 JSON 對應)
CHEMICAL_ITEMS = [
    "Pb", "Cd", "Hg", "CrVI", 
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "Cl", "Br", "I", "PFOS"
]

PRIORITY_MAP = {
    "number": 3, 
    "negative": 2, 
    "report": 2, 
    "nd": 1, 
    "none": 0
}

# =====================
# 2. PDF 文字擷取 (加入 x_tolerance 優化表格)
# =====================
def extract_text(file):
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            for i, p in enumerate(pdf.pages):
                if i > 7: break 
                # x_tolerance=1 可以幫助 pdfplumber 更好地區分表格欄位，避免字黏在一起
                t = p.extract_text(x_tolerance=1)
                if t:
                    text += t + "\n"
    except Exception:
        return ""
        
    if not text.strip():
        return ""

    # 強制 UTF-8 且忽略大小寫差異的干擾 (統一轉格式給 AI 看，但這裡先保持原樣讓 AI 判斷語意)
    return text.encode("utf-8", "ignore").decode("utf-8")

# =====================
# 3. 日期擷取
# =====================
def extract_date(text):
    patterns = [
        r"\b20\d{2}[-/]\d{2}[-/]\d{2}\b",       
        r"\b\d{2}[-/]\d{2}[-/]20\d{2}\b",       
        r"\b\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+20\d{2}\b" 
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group()
    return None

# =====================
# 4. AI 解析 (關鍵字強制對應版)
# =====================
def parse_with_ai(text):
    if not text:
        return {}

    truncated_text = text[:12000] # 再加大一點範圍
    
    prompt = f"""
你是一位檢測報告數據提取專家。請分析以下文字，並回傳精確的 JSON 格式數據。

=== ⚠️ 強制對應規則 (Case Insensitive) ===
請忽略大小寫，並將報告中出現的以下名稱，統一填入對應的 JSON 欄位：

1. **Pb** (JSON Key: "Pb"): 包含 Lead, Pb, Lead (Pb)
2. **Cd** (JSON Key: "Cd"): 包含 Cadmium, Cd, Cadmium (Cd)
3. **Hg** (JSON Key: "Hg"): 包含 Mercury, Hg, Mercury (Hg)
4. **CrVI** (JSON Key: "CrVI"): 包含 Hexavalent Chromium, Cr(VI), Cr6+, 六價鉻
5. **PBBs** (JSON Key: "pbbs_list"): 包含 Polybrominated Biphenyls, Monobromobiphenyl 等所有 PBB 子項。
6. **PBDEs** (JSON Key: "pbdes_list"): 包含 Polybrominated Diphenyl Ethers, Monobromodiphenyl ether 等所有 PBDE 子項。
7. **F** (JSON Key: "F"): 包含 Fluorine, F
8. **Cl** (JSON Key: "Cl"): 包含 Chlorine, Cl
9. **Br** (JSON Key: "Br"): 包含 Bromine, Br
10. **I** (JSON Key: "I"): 包含 Iodine, I
11. **PFOS** (JSON Key: "PFOS"): 包含 Perfluorooctane Sulfonates

=== 判讀邏輯 ===
1. **數值優先**: 尋找 "Result" 或 "Test Result" 欄位下的數值。
2. **N.D. 處理**: 若結果為 "N.D.", "ND", "Not Detected", "None", "<RL", "<5" -> 回傳 "N.D."
3. **Negative 處理**: 若結果為 "Negative" -> 回傳 "NEGATIVE"
4. **單位去除**: 抓取數值時，請自動移除 mg/kg, ppm 等單位，只留數字。
5. **PFAS 判斷**: 只要在報告標題、測試要求 (Test Requested) 中看到 "PFAS" 字眼，"pfas_detected" 就填 true。

=== 必須回傳的 JSON 結構 (Key 必須完全一致) ===
{{
  "items": {{
    "Pb": "N.D.",
    "Cd": "10.5",
    "Hg": "N.D.",
    "CrVI": "NEGATIVE",
    "DEHP": "N.D.",
    "BBP": "N.D.",
    "DBP": "N.D.",
    "DIBP": "N.D.",
    "F": "N.D.",
    "Cl": "N.D.",
    "Br": "N.D.",
    "I": "N.D.",
    "PFOS": "N.D."
  }},
  "pbbs_list": ["N.D."], 
  "pbdes_list": ["N.D."],
  "pfas_detected": true
}}

=== 報告內容 ===
{truncated_text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0, # 溫度設為 0 確保最精確的回答
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {}

# =====================
# 5. 數據處理
# =====================
def normalize_value(val):
    if val is None:
        return {"type": "none", "value": None}
    
    # 轉字串並轉大寫，解決 case sensitivity
    v_str = str(val).strip().upper()
    
    # 常見的「無檢出」關鍵字
    if v_str in ["N.D.", "ND", "NOT DETECTED", "NONE", "NULL", ""] or v_str.startswith("<"):
        return {"type": "nd", "value": "N.D."}
    
    if "NEGATIVE" in v_str:
        return {"type": "negative", "value": "NEGATIVE"}

    try:
        # 強力清洗：移除逗號、單位，只留數字和小數點
        clean_v = re.sub(r"[^\d\.]", "", v_str)
        # 避免空字串報錯
        if not clean_v:
            return {"type": "none", "value": None}
        float_v = float(clean_v)
        return {"type": "number", "value": float_v}
    except:
        return {"type": "none", "value": None}

def calculate_sum(value_list):
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
        # 修正：PBBs/PBDEs 若有值，顯示浮點數 (可視需求改為 int)
        return {"type": "number", "value": float(f"{total:.4g}")} # .4g 避免太多小數位
    else:
        return {"type": "nd", "value": "N.D."}

def compare_and_pick_best(current_best, new_val, file_name):
    new_obj = {
        "value": new_val["value"],
        "type": new_val["type"],
        "file": file_name
    }

    if current_best is None:
        return new_obj
    
    old_p = PRIORITY_MAP.get(current_best["type"], 0)
    new_p = PRIORITY_MAP.get(new_val["type"], 0)
    
    if new_p > old_p:
        return new_obj
    
    if new_p == old_p and new_val["type"] == "number":
        if new_val["value"] > current_best["value"]:
            return new_obj
            
    return current_best

# =====================
# 6. Streamlit UI
# =====================
st.set_page_config(page_title="SGS/CTI 報告彙總系統 v4.0", layout="wide")
st.title("🧪 第三方檢測報告自動彙總系統 v4.0")
st.markdown("支援 CTI/SGS/Intertek 報告，已優化大小寫與別名判讀。")

uploaded_files = st.file_uploader("請上傳 PDF 報告", type="pdf", accept_multiple_files=True)

if uploaded_files:
    final_results = {item: None for item in ITEMS_ORDER}
    errors = []
    
    # Debug 模式
    debug_expander = st.expander("🕵️‍♂️ 開發者偵錯模式 (AI 讀到的原始文字)", expanded=False)

    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, f in enumerate(uploaded_files):
        try:
            safe_filename = f.name.encode("utf-8", "ignore").decode("utf-8")
        except:
            safe_filename = f"file_{idx}.pdf"
            
        status_text.text(f"正在分析 ({idx+1}/{len(uploaded_files)}): {safe_filename} ...")
        
        try:
            # 1. 提取文字
            raw_text = extract_text(f)
            
            with debug_expander:
                st.markdown(f"**[{safe_filename}]** 前 800 字預覽:")
                st.code(raw_text[:800]) 
                st.divider()

            if not raw_text:
                raise ValueError("無法提取文字")

            # 2. 抓日期
            date_found = extract_date(raw_text)
            if date_found and final_results.get("DATE") is None:
                final_results["DATE"] = {"value": date_found, "type": "string", "file": safe_filename}

            # 3. AI 解析
            ai_data = parse_with_ai(raw_text)
            
            # 確保 items 存在，防止 CTI 報告回傳結構錯誤
            items_dict = ai_data.get("items", {})

            # 單一項目比對
            for key in CHEMICAL_ITEMS:
                # 這裡最重要：用我們定義的 Key 去字典裡抓，如果 AI 漏抓，視為 None
                val = items_dict.get(key)
                norm_val = normalize_value(val)
                final_results[key] = compare_and_pick_best(final_results[key], norm_val, safe_filename)

            # PBBs / PBDEs
            pbb_res = calculate_sum(ai_data.get("pbbs_list", []))
            final_results["PBBs"] = compare_and_pick_best(final_results["PBBs"], pbb_res, safe_filename)
            
            pbde_res = calculate_sum(ai_data.get("pbdes_list", []))
            final_results["PBDEs"] = compare_and_pick_best(final_results["PBDEs"], pbde_res, safe_filename)

            # PFAS
            if ai_data.get("pfas_detected") is True:
                pfas_val = {"type": "report", "value": "REPORT"}
                final_results["PFAS"] = compare_and_pick_best(final_results["PFAS"], pfas_val, safe_filename)
            else:
                pfas_nd = {"type": "nd", "value": "N.D."}
                final_results["PFAS"] = compare_and_pick_best(final_results["PFAS"], pfas_nd, safe_filename)

        except Exception as e:
            err_msg = str(e).encode("utf-8", "ignore").decode("utf-8")
            errors.append({"檔案": safe_filename, "錯誤": err_msg})
        
        progress_bar.progress((idx + 1) / len(uploaded_files))

    # =====================
    # 產出報表
    # =====================
    status_text.text("分析完成！")
    
    display_row = {}
    display_row["ITEM"] = "RESULT"
    
    for k in CHEMICAL_ITEMS + ["PBBs", "PBDEs", "PFAS"]:
        res = final_results.get(k)
        if res and res["value"] is not None:
            val = res["value"]
            # 讓整數顯示更漂亮 (10.0 -> 10)
            if isinstance(val, float) and val.is_integer():
                display_row[k] = int(val)
            else:
                display_row[k] = val
        else:
            # 如果還是 None，強迫顯示空白，而不是 Python 的 None
            display_row[k] = "" 

    date_res = final_results.get("DATE")
    display_row["DATE"] = date_res["value"] if date_res else ""

    pb_res = final_results.get("Pb")
    if pb_res and pb_res.get("file"):
        display_row["檔案名稱"] = pb_res["file"]
    else:
        display_row["檔案名稱"] = uploaded_files[0].name if uploaded_files else ""

    df = pd.DataFrame([display_row])
    
    for col in ITEMS_ORDER:
        if col not in df.columns:
            df[col] = ""
    df = df[ITEMS_ORDER]

    st.subheader("📊 彙總結果")
    st.dataframe(df, use_container_width=True)

    if errors:
        st.warning("⚠️ 部分檔案解析異常：")
        st.dataframe(pd.DataFrame(errors), use_container_width=True)
    
    progress_bar.empty()
    status_text.empty()
