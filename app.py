import streamlit as st
import os
import json
import re
import pandas as pd
import pymupdf4llm
import google.generativeai as genai
import tempfile

# ==========================================
# 1. 核心功能 (Google Gemini)
# ==========================================
def analyze_report_with_gemini(api_key, text, filename):
    try:
        genai.configure(api_key=api_key)
        
        # 優先嘗試 Flash 模型，這是目前性價比最高的
        # 如果您的帳號較舊，可能需要改用 gemini-pro
        model_name = 'gemini-1.5-flash' 
        model = genai.GenerativeModel(model_name)

        response_schema = {
            "type": "object",
            "properties": {
                "Pb": {"type": "string"}, "Cd": {"type": "string"}, "Hg": {"type": "string"},
                "Cr6": {"type": "string"}, "PBBs": {"type": "string"}, "PBDEs": {"type": "string"},
                "DEHP": {"type": "string"}, "BBP": {"type": "string"}, "DBP": {"type": "string"},
                "DIBP": {"type": "string"}, "F": {"type": "string"}, "Cl": {"type": "string"},
                "Br": {"type": "string"}, "I": {"type": "string"}, "PFOS": {"type": "string"},
                "PFAS_Status": {"type": "string"}, "DATE": {"type": "string"}
            }
        }

        prompt = f"""
        Analyze the chemical test report in Markdown below from "{filename}". Extract data to JSON.
        Rules:
        1. "ND", "N.D.", "< MDL" -> "N.D."
        2. Cr(VI) "Negative" -> "NEGATIVE"
        3. PBBs/PBDEs: Sum sub-items. If all ND, return "N.D."
        4. PFAS: "REPORT" only if exact "PFAS" keyword in requested list. Else null.
        5. Date: YYYY-MM-DD format.
        
        Content:
        {text[:30000]} 
        """
        # 注意：限制字數避免超過 token 上限 (雖然 Flash 額度很高，但安全起見)

        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=response_schema
            )
        )
        return json.loads(response.text)

    except Exception as e:
        st.error(f"❌ 解析失敗: {e}")
        # 如果失敗，嘗試列出可用的模型，幫助除錯
        try:
            st.warning("正在嘗試列出您帳號可用的模型...")
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    st.write(f"- {m.name}")
        except:
            st.error("無法列出模型，請檢查 API Key 是否正確。")
        return None

# ==========================================
# 2. 輔助功能
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
    fields = ["Pb", "Cd", "Hg", "Cr6", "PBBs", "PBDEs", "DEHP", "BBP", "DBP", "DIBP", "F", "Cl", "Br", "I", "PFOS", "PFAS_Status", "DATE"]
    final_data = {f: "" for f in fields}
    
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
    final_data['DATE'] = main_data.get("DATE")
    return final_data, best_filename

# ==========================================
# 3. Streamlit 介面
# ==========================================
st.set_page_config(page_title="通用檢測報告擷取 (Gemini版)", layout="wide")
st.title("🧪 通用型第三方檢測報告擷取工具 (Google Gemini版)")
st.markdown("支援 SGS, CTI, Intertek 格式。")

with st.sidebar:
    st.header("設定")
    api_key = st.text_input("請輸入 Google AI Studio API Key", type="password")
    st.info("請確保 API Key 有效且未過期。")

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
                    st.warning(f"⚠️ {uploaded_file.name} 內容過少，跳過。")
                else:
                    result = analyze_report_with_gemini(api_key, md_text, uploaded_file.name)
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
            
            st.success("✅ 擷取成功")
            st.dataframe(df, hide_index=True)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 下載 Excel (CSV)", csv, 'report_summary.csv', 'text/csv')
        else:
            st.error("❌ 無數據或發生錯誤，請查看上方警告訊息。")

elif not api_key:
    st.info("請在左側輸入 Google API Key。")
