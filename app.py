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

# AI 需回傳的標準 Key
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
# 2. PDF 文字擷取 (保留 Layout 以便閱讀表格)
# =====================
def extract_text(file):
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            # 讀取前 20 頁 (確保 Test Requested 和 Result 都在範圍內)
            max_pages = 20 
            for i, p in enumerate(pdf.pages):
                if i >= max_pages: break 
                
                # layout=True 能保留表格的物理位置，幫助 AI 對齊欄位
                t = p.extract_text(layout=True) 
                if t:
                    text += f"--- Page {i+1} ---\n{t}\n"
    except Exception:
        return ""
        
    if not text.strip():
        return ""

    # 強制 UTF-8
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
# 4. AI 解析 (v6.0 核心升級)
# =====================
def parse_with_ai(text):
    if not text:
        return {}

    # 擴大 Context Window
    truncated_text = text[:16000] 
    
    prompt = f"""
你是一位檢測報告數據提取專家。請分析以下 PDF 文字，提取化學檢測結果。

=== 1. PFAS 判讀規則 (最重要) ===
請仔細閱讀報告中的 **"Test Requested" (測試需求)**、**"Test Conducted" (測試內容)** 或 **"Sample Description"** 區塊。
**只要**在這些「測試範圍描述」中發現以下關鍵字，請將 "pfas_detected" 設為 true：
- "PFAS"
- "Per- and polyfluoroalkyl substances"
- "全氟/多氟烷基物質"
*注意：不需要看到具體數值，只要「測試項目」裡有提到要測 PFAS，就視為 true。*

=== 2. 化學物質數值提取 ===
請忽略 MDL (偵測極限)、RL (報告極限) 和 Limit (限值)。**只抓取 "Result" (結果) 欄位**。

**針對 CTI/SGS 報告的欄位陷阱：**
- 表格可能會黏在一起，例如 "Pb 10 N.D." (10 是 MDL，N.D. 才是結果)。
- 請優先找尋 "N.D." 或 "Negative" 或 具體數值。
- 若有多個數字，通常 **最後一個** 或 **數值較大** 的那個不是結果(通常是限值)，請仔細依據表頭判斷。

**關鍵字對應 (忽略大小寫):**
- Pb: Lead, 鉛
- Cd: Cadmium, 鎘
- CrVI: Hexavalent Chromium, Cr(VI), 六價鉻
- PBBs / PBDEs: 請列出該類別下所有子項目的結果。

=== 3. 輸出 JSON 格式 ===
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
        # 優先使用 gpt-4o-mini (更聰明、更便宜、更適合讀複雜表格)
        model_to_use = "gpt-4o-mini"
        
        response = client.chat.completions.create(
            model=model_to_use, 
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        # Fallback 到 gpt-3.5-turbo
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo", 
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except:
            return {}

# =====================
# 5. 數據處理
# =====================
def normalize_value(val):
    if val is None:
        return {"type": "none", "value": None}
    
    v_str = str(val).strip().upper()
    
    if v_str in ["N.D.", "ND", "NOT DETECTED", "NONE", "NULL", ""] or v_str.startswith("<"):
        return {"type": "nd", "value": "N.D."}
    
    if "NEGATIVE" in v_str:
        return {"type": "negative", "value": "NEGATIVE"}

    try:
        clean_v = re.sub(r"[^\d\.]", "", v_str)
        if not clean_v: return {"type": "none", "value": None}
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
        return {"type": "number", "value": float(f"{total:.4g}")}
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
st.set_page_config(page_title="SGS/CTI 報告彙總系統 v6.0", layout="wide")
st.title("🧪 第三方檢測報告自動彙總系統 v6.0")
st.info("已更新：PFAS 鎖定 'Test Requested' 判讀，並強化 CTI 表格數值抓取。")

uploaded_files = st.file_uploader("請上傳 PDF 報告", type="pdf", accept_multiple_files=True)

if uploaded_files:
    final_results = {item: None for item in ITEMS_ORDER}
    errors = []
    
    debug_expander = st.expander("🕵️‍♂️ 開發者偵錯模式 (查看 AI 讀到的內容)", expanded=False)

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
                st.markdown(f"**[{safe_filename}]** 前 1000 字預覽:")
                st.code(raw_text[:1000]) 
                st.divider()

            if not raw_text:
                raise ValueError("無法提取文字")

            # 2. 日期
            date_found = extract_date(raw_text)
            if date_found and final_results.get("DATE") is None:
                final_results["DATE"] = {"value": date_found, "type": "string", "file": safe_filename}

            # 3. AI 解析
            ai_data = parse_with_ai(raw_text)
            items_dict = ai_data.get("items", {})

            # 單一項目
            for key in CHEMICAL_ITEMS:
                val = items_dict.get(key)
                norm_val = normalize_value(val)
                final_results[key] = compare_and_pick_best(final_results[key], norm_val, safe_filename)

            # PBBs / PBDEs
            pbb_res = calculate_sum(ai_data.get("pbbs_list", []))
            final_results["PBBs"] = compare_and_pick_best(final_results["PBBs"], pbb_res, safe_filename)
            
            pbde_res = calculate_sum(ai_data.get("pbdes_list", []))
            final_results["PBDEs"] = compare_and_pick_best(final_results["PBDEs"], pbde_res, safe_filename)

            # PFAS (若 Test Requested 有提到，顯示 REPORT)
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
