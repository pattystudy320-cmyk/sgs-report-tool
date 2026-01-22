import streamlit as st
import os
import json
import re
import pandas as pd
import pymupdf4llm
import requests
import tempfile

# ==========================================
# 1. 核心功能 (鎖定 Gemini 2.0 Flash + v1beta)
# ==========================================
def analyze_report_direct(api_key, text, filename):
    # 這裡直接指定您的帳號支援的最新模型：gemini-2.0-flash
    # 並且強制使用 v1beta 接口
    model_name = "gemini-2.0-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    # 提示詞 (Prompt)
    prompt = f"""
    You are a chemical test report parser. 
    Task: Extract specific data from the document "{filename}" into JSON format.
    
    ### Extraction Rules:
    1. **Output ONLY JSON**. No Markdown (```json), no intro text.
    2. **Value Standardization**:
       - "ND", "N.D.", "< MDL", "Not Detected" -> "N.D."
       - "Negative" -> "NEGATIVE"
       - Remove units (mg/kg, ppm).
    3. **Logic**:
       - **PBBs/PBDEs**: Sum of sub-items. If all ND, return "N.D."
       - **PFAS_Status**: Check "Test Requested" section. ONLY if strict keyword "PFAS" or "Per- and Polyfluoroalkyl" is found, set to "REPORT". Else null.
    
    ### JSON Keys to Extract:
    - "Pb", "Cd", "Hg", "Cr6"
    - "PBBs", "PBDEs"
    - "DEHP", "BBP", "DBP", "DIBP"
    - "F", "Cl", "Br", "I"
    - "PFOS"
    - "PFAS_Status"
    - "DATE" (YYYY-MM-DD)

    ### Content:
    {text[:30000]}
    """

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    headers = {'Content-Type': 'application/json'}

    try:
        # 發送請求
        response = requests.post(url, headers=headers, json=payload)
        
        # 錯誤處理
        if response.status_code != 200:
            st.error(f"❌ API Error ({response.status_code}): {response.text}")
            return None
            
        result = response.json()
        
        # 解析內容
        try:
            raw_text = result['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            st.error(f"❌ 解析失敗，AI 未回傳內容。回應: {result}")
            return None

        # 清理 JSON 字串
        raw_text = raw_text.strip()
        # 移除可能的 Markdown 標記
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        
        return json.loads(raw_text)

    except Exception as e:
        st.error(f"❌ 處理例外狀況: {e}")
        return None

# ==========================================
# 2. 輔助功能 (無需變動)
# ==========================================
def get_score(value):
    if not value: return 0
    v = str(value).strip().upper()
    if v == "REPORT": return 9999
    if "N.D" in v or "ND" in v or "<" in v: return 1
    if "NEG" in v: return 2
    try:
        match = re.search(r"[-+]?\d*\.\d+|\d+", v)
        if match: return 100 + float(match.group())
        return 0
    except: return 0

def merge_results(results_list):
    if not results_list: return None, ""
    fields = ["Pb", "Cd", "Hg", "Cr6", "PBBs", "PBDEs", "DEHP", "BBP", "DBP", "DIBP", "F", "Cl", "Br", "I", "PFOS", "PFAS_Status", "DATE"]
    final_data = {f: "" for f in fields}
    
    for field in fields:
        if field == "DATE": continue
        best_val = ""
        best_score = -1
        for item in results_list:
            val = item['data'].get(field, "")
            score = get_score(val)
            if score > best_score:
                best_score = score
                best_val = val
        final_data[field] = best_val if best_val else ""

    best_filename = results_list[0]['filename']
    max_pb_score = -1
    max_total_score = -1
    for item in results_list:
        data = item['data']
        pb_score = get_score(data.get("Pb"))
        total_score = sum(get_score(data.get(f)) for f in fields if f != "DATE")
        if pb_score > max_pb_score:
            max_pb_score = pb_score
            max_total_score = total_score
            best_filename = item['filename']
        elif pb_score == max_pb_score:
            if total_score > max_total_score:
                max_total_score = total_score
                best_filename = item['filename']

    main_data = next(r['data'] for r in results_list if r['filename'] == best_filename)
    final_data['DATE'] = main_data.get("DATE", "")
    return final_data, best_filename

# ==========================================
# 3. Streamlit 介面
# ==========================================
st.set_page_config(page_title="檢測報告擷取 (Gemini 2.0)", layout="wide")
st.title("🧪 通用型第三方檢測報告擷取工具 (Gemini 2.0版)")
st.markdown("使用最新 Gemini 2.0 Flash 模型 + v1beta 接口。")

with st.sidebar:
    st.header("設定")
    api_key = st.text_input("請輸入 Google AI Studio API Key", type="password")

uploaded_files = st.file_uploader("請上傳 PDF 報告", type=["pdf"], accept_multiple_files=True)

if uploaded_files and api_key:
    if st.button("開始分析", type="primary"):
        processed_files = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, uploaded_file in enumerate(uploaded_files):
            status_text.text(f"正在讀取: {uploaded_file.name} ...")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.read())
                tmp_path = tmp_file.name

            try:
                md_text = pymupdf4llm.to_markdown(tmp_path)
                if len(md_text) < 50:
                    st.warning(f"⚠️ {uploaded_file.name} 內容過少，跳過。")
                else:
                    result = analyze_report_direct(api_key, md_text, uploaded_file.name)
                    if result:
                        processed_files.append({"filename": uploaded_file.name, "data": result})
            finally:
                os.remove(tmp_path)
            progress_bar.progress((i + 1) / len(uploaded_files))

        status_text.text("分析完成！")
        
        if processed_files:
            merged_data, primary_filename = merge_results(processed_files)
            
            table_row = {
                "ITEM": "1",
                "Pb": merged_data.get("Pb"), "Cd": merged_data.get("Cd"), "Hg": merged_data.get("Hg"),
                "Cr+6": merged_data.get("Cr6"), "PBBs": merged_data.get("PBBs"), "PBDEs": merged_data.get("PBDEs"),
                "DEHP": merged_data.get("DEHP"), "BBP": merged_data.get("BBP"), "DBP": merged_data.get("DBP"),
                "DIBP": merged_data.get("DIBP"), "F": merged_data.get("F"), "Cl": merged_data.get("Cl"),
                "Br": merged_data.get("Br"), "I": merged_data.get("I"), "PFOS": merged_data.get("PFOS"),
                "PFAS": merged_data.get("PFAS_Status"), "DATE": merged_data.get("DATE"),
                "FILE NAME": primary_filename
            }
            
            cols = ["ITEM", "Pb", "Cd", "Hg", "Cr+6", "PBBs", "PBDEs", "DEHP", "BBP", 
                    "DBP", "DIBP", "F", "Cl", "Br", "I", "PFOS", "PFAS", "DATE", "FILE NAME"]
            df = pd.DataFrame([table_row])
            for c in cols:
                if c not in df.columns: df[c] = ""
            df = df[cols]
            
            st.success(f"✅ 擷取成功 (Primary File: {primary_filename})")
            st.dataframe(df, hide_index=True)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 下載 Excel (CSV)", csv, 'report_summary.csv', 'text/csv')
        else:
            st.error("❌ 無數據或發生錯誤。")

elif not api_key:
    st.info("請在左側輸入 Google API Key。")
