import streamlit as st
import os
import json
import re
import pandas as pd
import pymupdf4llm
import requests
import tempfile

# ==========================================
# 1. 核心功能 (智慧型自動偵測模型)
# ==========================================
def get_valid_model(api_key):
    """
    自動詢問 Google 帳號可用的模型，並回傳最適合的一個。
    """
    # 嘗試查詢模型列表 (使用 v1beta 以獲得最新列表)
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            models = [m['name'] for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
            
            # 優先順序策略
            # 1. 嘗試找 1.5 Flash (速度快、免費額度高)
            for m in models:
                if 'gemini-1.5-flash' in m and 'latest' not in m and 'exp' not in m:
                    return m, "v1beta" # Flash 通常在 beta 比較新
            
            # 2. 嘗試找 1.5 Pro
            for m in models:
                if 'gemini-1.5-pro' in m and 'latest' not in m:
                    return m, "v1beta"

            # 3. 保底使用最穩定的 1.0 Pro
            for m in models:
                if 'gemini-pro' in m or 'gemini-1.0-pro' in m:
                    return m, "v1" # Pro 在 v1 最穩定
            
            # 4. 如果都沒找到，隨便回傳第一個
            if models:
                return models[0], "v1beta"
                
    except Exception as e:
        print(f"模型列表查詢失敗: {e}")
    
    # 如果查詢失敗，回傳一個最保守的預設值
    return "models/gemini-1.0-pro", "v1"

def analyze_report_smart(api_key, text, filename):
    # 第一步：自動取得可用模型
    model_name, api_version = get_valid_model(api_key)
    
    # 確保模型名稱格式正確 (移除 models/ 前綴以免重複)
    clean_model_name = model_name.replace("models/", "")
    
    # 建立連線網址
    url = f"https://generativelanguage.googleapis.com/{api_version}/models/{clean_model_name}:generateContent?key={api_key}"
    
    # 提示詞
    prompt = f"""
    You are a data extraction assistant. Extract data from the document "{filename}".
    Return ONLY valid JSON. No Markdown. No explanations.
    
    Extract these exact keys:
    - "Pb", "Cd", "Hg", "Cr6", "PBBs", "PBDEs", "DEHP", "BBP", "DBP", "DIBP" (Value or "N.D.")
    - "F", "Cl", "Br", "I", "PFOS" (Value or "N.D.")
    - "PFAS_Status" ("REPORT" if keyword "PFAS" found in request list, else null)
    - "DATE" (YYYY-MM-DD)

    Content:
    {text[:28000]}
    """

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {'Content-Type': 'application/json'}

    try:
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            # 如果失敗，顯示詳細錯誤以便除錯
            st.error(f"❌ API Error ({clean_model_name}): {response.text}")
            return None
            
        result = response.json()
        
        # 嘗試解析
        try:
            raw_text = result['candidates'][0]['content']['parts'][0]['text']
        except:
            st.error("❌ 無法讀取 AI 回傳內容，可能是內容被 Google 安全過濾攔截。")
            return None

        # 清理 JSON
        raw_text = raw_text.strip()
        if "```json" in raw_text: raw_text = raw_text.replace("```json", "").replace("```", "")
        elif "```" in raw_text: raw_text = raw_text.replace("```", "")
            
        return json.loads(raw_text)

    except Exception as e:
        st.error(f"❌ 處理失敗: {e}")
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
st.set_page_config(page_title="檢測報告擷取 (智慧偵測版)", layout="wide")
st.title("🧪 通用型第三方檢測報告擷取工具 (智慧偵測版)")
st.markdown("自動偵測可用模型，解決 404 錯誤。")

with st.sidebar:
    st.header("設定")
    api_key = st.text_input("請輸入 Google AI Studio API Key", type="password")

uploaded_files = st.file_uploader("請上傳 PDF 報告", type=["pdf"], accept_multiple_files=True)

if uploaded_files and api_key:
    if st.button("開始分析", type="primary"):
        processed_files = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # 測試連線並顯示目前使用的模型
        test_model, test_ver = get_valid_model(api_key)
        st.toast(f"已連線至模型: {test_model} ({test_ver})")
        
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
                    result = analyze_report_smart(api_key, md_text, uploaded_file.name)
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
            st.error("❌ 無數據或發生錯誤。")

elif not api_key:
    st.info("請在左側輸入 Google API Key。")
