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

# 欄位顯示順序
ITEMS_ORDER = [
    "ITEM", "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "F", "Cl", "Br", "I", "PFOS", "PFAS", 
    "DATE", "檔案名稱"
]

# AI 需要識別的化學鍵值
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
# 2. PDF 文字擷取 (擴大範圍版)
# =====================
def extract_text(file):
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            # 擴大讀取前 8 頁，通常檢測結果都在這範圍內
            # CTI 或 SGS 有時前面有很多聲明，讀多一點比較保險
            for i, p in enumerate(pdf.pages):
                if i > 7: break 
                t = p.extract_text()
                if t:
                    text += t + "\n"
    except Exception:
        return ""
        
    if not text.strip():
        return ""

    # 強制 UTF-8 處理，避免編碼錯誤
    return text.encode("utf-8", "ignore").decode("utf-8")

# =====================
# 3. 日期擷取
# =====================
def extract_date(text):
    # 針對各種日期格式的 Regex
    patterns = [
        r"\b20\d{2}[-/]\d{2}[-/]\d{2}\b",       # 2024-01-01
        r"\b\d{2}[-/]\d{2}[-/]20\d{2}\b",       # 01-01-2024
        r"\b\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+20\d{2}\b" # 05 Jan 2024
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group()
    return None

# =====================
# 4. AI 解析 (加入別名與增強邏輯)
# =====================
def parse_with_ai(text):
    if not text:
        return {}

    # 擴大到 10000 字元，確保不會漏掉表格
    truncated_text = text[:10000] 
    
    prompt = f"""
你是一位專業的化學檢測報告分析師。請分析以下報告內容，並提取數值。

=== ⚠️ 關鍵識別規則 (必讀) ===
報告中的化學物質可能使用全名，請務必進行對應：
- Pb = Lead (鉛)
- Cd = Cadmium (鎘)
- Hg = Mercury (汞)
- CrVI = Hexavalent Chromium (六價鉻)
- PBBs = Polybrominated Biphenyls (多溴聯苯) -> 需加總
- PBDEs = Polybrominated Diphenyl Ethers (多溴二苯醚) -> 需加總
- F = Fluorine (氟)
- Cl = Chlorine (氯)
- Br = Bromine (溴)
- I = Iodine (碘)

=== 提取邏輯 ===
1. **單一物質**: 提取測試結果 (Result)。
2. **PBBs / PBDEs**: 這通常是一組數據 (如 Monobromobiphenyl, DecaBDE 等)。請將該類別下「所有測出的子項目數值」列在清單中。若全為 N.D.，清單內放 "N.D." 即可。
3. **PFAS**: 檢查 "Test Requested" (測試要求) 或 "Test Part Description" (測試部位) 或報告標題。只要出現 "PFAS" 或 "Per- and polyfluoroalkyl substances" 字眼，請回傳 true。

=== 數值正規化 ===
- 若結果為 "N.D.", "ND", "n.d.", "Not Detected", "<RL", "<MDL", "< 5" -> JSON 數值填 "N.D."
- 若結果為 "Negative" -> JSON 數值填 "NEGATIVE"
- 若有具體數值 (如 "10.5", "5 mg/kg") -> JSON 只回傳數字 "10.5" (去單位)
- **千萬不要** 把 MDL (Method Detection Limit) 或 RL (Reporting Limit) 當成結果。請找 "Result" 或 "Test Result" 欄位。

=== 輸出 JSON 格式 ===
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
  "pbbs_list": ["N.D.", "5"], 
  "pbdes_list": ["N.D."],
  "pfas_detected": true
}}

=== 報告內容 ===
{truncated_text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo", # 如果預算允許，改用 "gpt-4-turbo" 判讀表格能力更強
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {}

# =====================
# 5. 數據處理與比對
# =====================
def normalize_value(val):
    if val is None:
        return {"type": "none", "value": None}
    
    v_str = str(val).strip().upper()
    
    if v_str in ["N.D.", "ND", "NOT DETECTED", "NONE"] or v_str.startswith("<"):
        return {"type": "nd", "value": "N.D."}
    
    if "NEGATIVE" in v_str:
        return {"type": "negative", "value": "NEGATIVE"}

    try:
        # 移除逗號 (1,000 -> 1000) 和非數字字符
        clean_v = re.sub(r"[^\d\.]", "", v_str.replace(",", ""))
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
        # PBBs/PBDEs 總和顯示，若有小數點保留兩位，整數則顯示整數
        return {"type": "number", "value": total}
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
    
    # 規則: 數值 > NEGATIVE > ND > None
    if new_p > old_p:
        return new_obj
    
    # 同等級比大小 (Max Value)
    if new_p == old_p and new_val["type"] == "number":
        if new_val["value"] > current_best["value"]:
            return new_obj
            
    return current_best

# =====================
# 6. Streamlit UI
# =====================
st.set_page_config(page_title="SGS/Intertek 報告彙總系統", layout="wide")
st.title("🧪 第三方檢測報告自動彙總系統 v3.0")
st.markdown("""
<style>
    .stDataFrame {font-size: 1.1rem;}
</style>
支援多份 PDF 上傳 (SGS, Intertek, CTI)，自動抓取最大值。
""", unsafe_allow_html=True)

uploaded_files = st.file_uploader("請上傳 PDF 報告", type="pdf", accept_multiple_files=True)

if uploaded_files:
    final_results = {item: None for item in ITEMS_ORDER}
    errors = []
    
    # 用來 Debug 的區塊
    debug_expander = st.expander("🕵️‍♂️ 開發者偵錯模式 (查看 AI 讀到了什麼)", expanded=False)

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
            
            # Debug: 顯示前 1000 個字確認有無讀到
            with debug_expander:
                st.markdown(f"**[{safe_filename}]** 前 500 字預覽:")
                st.text(raw_text[:500] + "...")
                st.divider()

            if not raw_text:
                raise ValueError("無法提取文字 (可能是圖片掃描檔或加密)")

            # 2. 抓日期
            date_found = extract_date(raw_text)
            if date_found and final_results.get("DATE") is None:
                final_results["DATE"] = {"value": date_found, "type": "string", "file": safe_filename}

            # 3. AI 解析
            ai_data = parse_with_ai(raw_text)
            
            # 單一項目
            items_dict = ai_data.get("items", {})
            for key in CHEMICAL_ITEMS:
                val = items_dict.get(key)
                norm_val = normalize_value(val)
                final_results[key] = compare_and_pick_best(final_results[key], norm_val, safe_filename)

            # PBBs / PBDEs 加總
            pbb_res = calculate_sum(ai_data.get("pbbs_list", []))
            final_results["PBBs"] = compare_and_pick_best(final_results["PBBs"], pbb_res, safe_filename)
            
            pbde_res = calculate_sum(ai_data.get("pbdes_list", []))
            final_results["PBDEs"] = compare_and_pick_best(final_results["PBDEs"], pbde_res, safe_filename)

            # PFAS (有測就是 REPORT)
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
            # 如果是浮點數，判斷是否為整數
            val = res["value"]
            if isinstance(val, float) and val.is_integer():
                display_row[k] = int(val)
            else:
                display_row[k] = val
        else:
            display_row[k] = "" 

    date_res = final_results.get("DATE")
    display_row["DATE"] = date_res["value"] if date_res else ""

    pb_res = final_results.get("Pb")
    if pb_res and pb_res.get("file"):
        display_row["檔案名稱"] = pb_res["file"]
    else:
        display_row["檔案名稱"] = uploaded_files[0].name if uploaded_files else ""

    df = pd.DataFrame([display_row])
    
    # 補齊缺少的欄位並排序
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
