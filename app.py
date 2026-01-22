import streamlit as st
import os
import json
import re
import pandas as pd
import pymupdf4llm
import google.generativeai as genai
import tempfile

# ==========================================
# 1. 核心功能 (最純粹的文字模式 - 高相容版)
# ==========================================
def analyze_report_with_gemini(api_key, text, filename):
    try:
        genai.configure(api_key=api_key)
        
        # 使用您列表上確認存在的模型名稱
        # 不使用 -latest，直接用標準名稱
        model = genai.GenerativeModel('gemini-1.5-flash')

        # 提示詞：明確要求純 JSON 字串
        prompt = f"""
        You are a data extraction assistant. 
        Extract chemical test results from the document "{filename}".
        
        Output Requirements:
        1. Return ONLY a valid JSON object.
        2. Do NOT use Markdown code blocks (no ```json).
        3. Do NOT include any explanation text.
        
        Data to Extract (use exact keys):
        - "Pb", "Cd", "Hg", "Cr6" (value or "N.D." or "NEGATIVE")
        - "PBBs", "PBDEs" (Sum of sub-items. If all ND, return "N.D.")
        - "DEHP", "BBP", "DBP", "DIBP" (value or "N.D.")
        - "F", "Cl", "Br", "I" (value or "N.D.")
        - "PFOS" (value or "N.D.")
        - "PFAS_Status" (Set to "REPORT" ONLY if "PFAS" keyword is in 'Test Requested'. Else null)
        - "DATE" (YYYY-MM-DD)

        Document Content:
        {text[:30000]}
        """

        # 重點修正：完全移除 generation_config
        # 這能避開 API 版本不支援 JSON Mode 的錯誤
        response = model.generate_content(prompt)
        
        # 手動清理回傳的文字 (AI 有時候還是會雞婆加 Markdown)
        raw_text = response.text.strip()
        
        # 移除可能的 Markdown 標記，確保是純 JSON
        if "```json" in raw_text:
            raw_text = raw_text.replace("```json", "").replace("```", "")
        elif "```" in raw_text:
            raw_text = raw_text.replace("```", "")
            
        return json.loads(raw_text)

    except Exception as e:
        st.error(f"❌ 解析失敗 ({filename}): {e}")
        return None

# ==========================================
# 2. 輔助功能 (邏輯維持不變)
# ==========================================
def get_score(value):
    if not value: return 0
    v = str(value).strip().upper()
    if v == "REPORT": return 9999
    if "N.D" in v or "ND" in v or "<" in v: return 1
    if "NEG" in v: return 2
    try:
        match = re.search(r"[-+]?\d*\.\d+|\d+", v)
        if match:
            return 100 + float(match.group())
        return 0
    except:
        return 0

def merge_results(results_list):
    if not results_list: return None, ""
    fields = ["Pb", "Cd", "Hg", "Cr6", "PBBs", "PBDEs", "DEHP", "BBP", "DBP", "DIBP", "F", "Cl", "Br", "I", "PFOS", "PFAS_Status", "DATE"]
    final_data = {f: "" for f in fields}
    
    # 數值合併邏輯
    for field in fields:
        if field == "DATE": continue
        best_val = ""
        best_score = -1
        for item in results_list:
            # 確保 key 存在，避免報錯
            val = item['data'].get(field, "")
            score = get_score(val)
            if score > best_score:
                best_score = score
                best_val = val
        final_data[field] = best_val if best_val else ""

    # 檔案選取邏輯
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
st.set_page_config(page_title="通用檢測報告擷取 (兼容模式)", layout="wide")
st.title("🧪 通用型第三方檢測報告擷取工具 (兼容模式)")
st.markdown("支援 SGS, CTI, Intertek 格式。已切換至高相容性模式。")

with st.sidebar:
    st.header("設定")
    api_key = st.text_input("請輸入 Google AI Studio API Key", type="password")

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
            
            # 對應您的 Excel 格式
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
            # 補齊空欄位
            for c in cols:
                if c not in df.columns: df[c] = ""
            df = df[cols]
            
            st.success("✅ 擷取成功")
            st.dataframe(df, hide_index=True)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 下載 Excel (CSV)", csv, 'report_summary.csv', 'text/csv')
        else:
            st.error("❌ 無數據或發生錯誤。")

elif not api_key:
    st.info("請在左側輸入 Google API Key。")
