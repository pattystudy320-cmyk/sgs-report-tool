import streamlit as st
import os
import json
import re
import pandas as pd
import pymupdf4llm
import requests
import tempfile
import time

# ==========================================
# 1. 核心功能 (自動切換模型版)
# ==========================================
def analyze_report_fallback(api_key, text, filename):
    
    # 定義我們要嘗試的「模型策略清單」
    # 順序：優先用額度高且穩定的 1.5 Flash (v1) -> 失敗則試 v1beta -> 再失敗試 1.0 Pro
    strategy_list = [
        ("gemini-1.5-flash", "v1"),          # 策略1: 1.5 Flash 正式版 (最穩)
        ("gemini-1.5-flash", "v1beta"),      # 策略2: 1.5 Flash 測試版
        ("gemini-1.5-flash-latest", "v1beta"), # 策略3: 1.5 Flash 最新版
        ("gemini-1.5-pro", "v1beta"),        # 策略4: 1.5 Pro (如果有額度)
        ("gemini-1.0-pro", "v1")             # 策略5: 舊版 Pro (保底)
    ]

    prompt = f"""
    You are a data extraction assistant. Extract chemical test results from "{filename}".
    Output JSON ONLY. No Markdown. No explanations.
    
    Keys to Extract:
    - "Pb", "Cd", "Hg", "Cr6", "PBBs", "PBDEs", "DEHP", "BBP", "DBP", "DIBP" (Value or "N.D.")
    - "F", "Cl", "Br", "I", "PFOS" (Value or "N.D.")
    - "PFAS_Status" ("REPORT" if keyword "PFAS" in request list, else null)
    - "DATE" (YYYY-MM-DD)

    Content:
    {text[:30000]}
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {'Content-Type': 'application/json'}

    last_error = ""

    # 開始輪詢策略
    for model_name, version in strategy_list:
        url = f"https://generativelanguage.googleapis.com/{version}/models/{model_name}:generateContent?key={api_key}"
        
        try:
            # 顯示目前正在嘗試的模型 (方便除錯)
            print(f"Trying {model_name} ({version})...") 
            
            response = requests.post(url, headers=headers, json=payload)
            
            # 情況 A: 成功 (200)
            if response.status_code == 200:
                result = response.json()
                try:
                    raw_text = result['candidates'][0]['content']['parts'][0]['text']
                    # 清理 JSON
                    raw_text = raw_text.strip()
                    if raw_text.startswith("```json"): raw_text = raw_text[7:]
                    if raw_text.endswith("```"): raw_text = raw_text[:-3]
                    
                    st.toast(f"✅ 使用模型成功: {model_name} ({version})")
                    return json.loads(raw_text)
                except:
                    continue # 解析失敗，換下一個

            # 情況 B: 額度不足 (429) -> 休息一下再試同一個，或跳過
            elif response.status_code == 429:
                last_error = f"429 Quota Exceeded ({model_name})"
                time.sleep(2) # 稍微休息
                continue # 換下一個模型試試

            # 情況 C: 找不到模型 (404) -> 直接換下一個
            elif response.status_code == 404:
                last_error = f"404 Not Found ({model_name} on {version})"
                continue

            else:
                last_error = f"Error {response.status_code}: {response.text}"
                continue

        except Exception as e:
            last_error = str(e)
            continue
            
    # 如果迴圈跑完都沒成功
    st.error(f"❌ 所有模型嘗試皆失敗。最後錯誤: {last_error}")
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
st.set_page_config(page_title="檢測報告擷取 (自動切換版)", layout="wide")
st.title("🧪 通用型第三方檢測報告擷取工具 (自動切換版)")
st.markdown("自動輪詢 Gemini 1.5 Flash (v1/v1beta) 與 1.0 Pro，確保連線成功。")

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
            
            # 避免觸發速率限制
            if i > 0: time.sleep(1) 
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.read())
                tmp_path = tmp_file.name

            try:
                md_text = pymupdf4llm.to_markdown(tmp_path)
                if len(md_text) < 50:
                    st.warning(f"⚠️ {uploaded_file.name} 內容過少，跳過。")
                else:
                    # 使用自動切換函式
                    result = analyze_report_fallback(api_key, md_text, uploaded_file.name)
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
            
            st.success(f"✅ 擷取成功")
            st.dataframe(df, hide_index=True)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 下載 Excel (CSV)", csv, 'report_summary.csv', 'text/csv')
        else:
            st.error("❌ 無數據或所有模型皆失敗。")

elif not api_key:
    st.info("請在左側輸入 Google API Key。")
