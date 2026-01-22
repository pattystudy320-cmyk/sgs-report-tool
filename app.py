import streamlit as st
import os
import json
import re
import pandas as pd
import pymupdf4llm
import google.generativeai as genai
import tempfile
import typing_extensions

# ==========================================
# 1. 核心功能 (改用 Google Gemini)
# ==========================================
def analyze_report_with_gemini(api_key, text, filename):
    # 設定 Google API
    genai.configure(api_key=api_key)
    
    # 使用輕量快速的 Flash 模型 (通常有免費額度)
    model = genai.GenerativeModel('gemini-1.5-flash')

    # 定義輸出的 JSON 結構 (Schema)
    # 這能確保 Google 回傳電腦看得懂的格式
    response_schema = {
        "type": "object",
        "properties": {
            "Pb": {"type": "string", "description": "Lead value"},
            "Cd": {"type": "string", "description": "Cadmium value"},
            "Hg": {"type": "string", "description": "Mercury value"},
            "Cr6": {"type": "string", "description": "Hexavalent Chromium value"},
            "PBBs": {"type": "string", "description": "Sum of PBBs"},
            "PBDEs": {"type": "string", "description": "Sum of PBDEs"},
            "DEHP": {"type": "string", "description": "DEHP value"},
            "BBP": {"type": "string", "description": "BBP value"},
            "DBP": {"type": "string", "description": "DBP value"},
            "DIBP": {"type": "string", "description": "DIBP value"},
            "F": {"type": "string", "description": "Fluorine value"},
            "Cl": {"type": "string", "description": "Chlorine value"},
            "Br": {"type": "string", "description": "Bromine value"},
            "I": {"type": "string", "description": "Iodine value"},
            "PFOS": {"type": "string", "description": "PFOS value"},
            "PFAS_Status": {"type": "string", "description": "REPORT if keyword found, else null"},
            "DATE": {"type": "string", "description": "Date YYYY-MM-DD"}
        }
    }

    prompt = f"""
    You are a chemical test report parser. Analyze the following Markdown content from file "{filename}" and extract data into JSON.

    ### Rules:
    1. **Standardize**: Convert "ND", "N.D.", "Not Detected", "< MDL" to "N.D.".
    2. **Cr(VI)**: If "Negative", return "NEGATIVE".
    3. **PBBs/PBDEs**: 
       - Look for sub-items (Mono- to Deca-). 
       - If numbers exist, SUM them up. 
       - If all sub-items are N.D., return "N.D.".
    4. **PFAS Strict Logic**: 
       - CHECK the "Test Requested" or "Test Requirement" section.
       - ONLY if the exact string "PFAS" or "Per- and Polyfluoroalkyl" appears there, set 'PFAS_Status' to "REPORT".
       - Otherwise set it to null (None).
    5. **Date**: Extract the report date in YYYY-MM-DD format.

    Report Content:
    {text}
    """

    try:
        # 呼叫 Gemini
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=response_schema
            )
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"Google Gemini 解析錯誤 ({filename}): {e}")
        return None

# ==========================================
# 2. 輔助邏輯 (分數計算與合併) - 維持不變
# ==========================================
def get_score(value):
    if not value: return 0
    v = str(value).strip().upper()
    if v == "REPORT": return 9999
    if "N.D" in v or "ND" in v or "<" in v: return 1
    if "NEG" in v: return 2
    try:
        num = float(re.findall(r"[-+]?\d*\.\d+|\d+", v)[0])
        return 100 + num
    except:
        return 0

def merge_results(results_list):
    if not results_list: return None, ""
    
    # 定義欄位清單
    fields = ["Pb", "Cd", "Hg", "Cr6", "PBBs", "PBDEs", "DEHP", "BBP", "DBP", "DIBP", "F", "Cl", "Br", "I", "PFOS", "PFAS_Status", "DATE"]
    final_data = {f: "" for f in fields}
    
    # 取最大值邏輯
    for field in fields:
        if field == "DATE": continue
        best_val = ""
        best_score = -1
        for item in results_list:
            val = item['data'].get(field)
            score = get_score(val)
            if score > best_score:
                best_score = score
                best_val = val
        final_data[field] = best_val if best_val else ""

    # 決定主要檔案名稱
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

    # 抓取主要檔案的日期
    main_data = next(r['data'] for r in results_list if r['filename'] == best_filename)
    final_data['DATE'] = main_data.get("DATE")
    
    return final_data, best_filename

# ==========================================
# 3. Streamlit 介面
# ==========================================
st.set_page_config(page_title="通用檢測報告擷取 (Gemini版)", layout="wide")

st.title("🧪 通用型第三方檢測報告擷取工具 (Google Gemini版)")
st.markdown("支援 SGS, CTI, Intertek 格式。使用 Google Gemini Flash 模型 (免費額度高)。")

# 側邊欄
with st.sidebar:
    st.header("設定")
    api_key = st.text_input("請輸入 Google AI Studio API Key", type="password")
    st.markdown("[👉 點此免費申請 Google API Key](https://aistudio.google.com/app/apikey)")
    st.markdown("---")
    st.info("此版本不使用 OpenAI，完全改用 Google Gemini。")

# 檔案上傳
uploaded_files = st.file_uploader("請上傳 PDF 報告 (可多選)", type=["pdf"], accept_multiple_files=True)

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
                    st.warning(f"⚠️ {uploaded_file.name} 疑似為圖片掃描檔，跳過。")
                else:
                    # 改呼叫 Gemini 函式
                    result = analyze_report_with_gemini(api_key, md_text, uploaded_file.name)
                    if result:
                        processed_files.append({"filename": uploaded_file.name, "data": result})
            finally:
                os.remove(tmp_path)
            
            progress_bar.progress((i + 1) / len(uploaded_files))

        status_text.text("分析完成！")
        
        if processed_files:
            merged_data, primary_filename = merge_results(processed_files)
            
            # 建立表格
            table_row = {
                "ITEM": "1",
                "Pb": merged_data.get("Pb"),
                "Cd": merged_data.get("Cd"),
                "Hg": merged_data.get("Hg"),
                "Cr+6": merged_data.get("Cr6"),
                "PBBs": merged_data.get("PBBs"),
                "PBDEs": merged_data.get("PBDEs"),
                "DEHP": merged_data.get("DEHP"),
                "BBP": merged_data.get("BBP"),
                "DBP": merged_data.get("DBP"),
                "DIBP": merged_data.get("DIBP"),
                "F": merged_data.get("F"),
                "Cl": merged_data.get("Cl"),
                "Br": merged_data.get("Br"),
                "I": merged_data.get("I"),
                "PFOS": merged_data.get("PFOS"),
                "PFAS": merged_data.get("PFAS_Status"),
                "DATE": merged_data.get("DATE"),
                "FILE NAME": primary_filename
            }
            
            cols = ["ITEM", "Pb", "Cd", "Hg", "Cr+6", "PBBs", "PBDEs", "DEHP", "BBP", 
                    "DBP", "DIBP", "F", "Cl", "Br", "I", "PFOS", "PFAS", "DATE", "FILE NAME"]
            
            df = pd.DataFrame([table_row])
            # 確保欄位存在 (避免某些欄位抓不到報錯)
            for c in cols:
                if c not in df.columns: df[c] = ""
            df = df[cols]
            
            st.success("✅ 擷取成功 (Powered by Google Gemini)")
            st.dataframe(df, hide_index=True)
            
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 下載 Excel (CSV)", csv, 'report_summary.csv', 'text/csv')
        else:
            st.error("❌ 未能提取任何數據。")

elif not api_key:
    st.info("請在左側輸入 Google API Key。")
