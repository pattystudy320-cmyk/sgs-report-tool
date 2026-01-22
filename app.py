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
# 1. 核心功能 (精準版號輪詢)
# ==========================================
def analyze_report_final(api_key, text, filename):
    
    # 這是根據您截圖(圖4)中顯示的確切模型清單
    # 我們不猜簡稱，直接打全名，並搭配對應的 API 版本
    strategy_list = [
        # 策略 1: 1.5 Flash 002 版 (通常最新最穩)
        ("gemini-1.5-flash-002", "v1beta"),
        # 策略 2: 1.5 Flash 001 版 (舊一點但很穩)
        ("gemini-1.5-flash-001", "v1beta"),
        # 策略 3: 1.5 Flash 8b (輕量版，速度快)
        ("gemini-1.5-flash-8b", "v1beta"),
        # 策略 4: 1.5 Pro 002 (如果 Flash 都不行，試試 Pro)
        ("gemini-1.5-pro-002", "v1beta"),
        # 策略 5: 簡稱備用
        ("gemini-1.5-flash", "v1beta"),
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
    {text[:28000]}
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {'Content-Type': 'application/json'}

    last_error = ""

    # 開始輪詢策略
    for model_name, version in strategy_list:
        url = f"https://generativelanguage.googleapis.com/{version}/models/{model_name}:generateContent?key={api_key}"
        
        try:
            # 在介面上顯示目前嘗試的模型，讓您知道進度
            print(f"嘗試連線: {model_name}...") 
            
            response = requests.post(url, headers=headers, json=payload)
            
            # --- 情況 A: 成功 (200) ---
            if response.status_code == 200:
                result = response.json()
                try:
                    raw_text = result['candidates'][0]['content']['parts'][0]['text']
                    # 清理 JSON
                    raw_text = raw_text.strip()
                    if raw_text.startswith("```json"): raw_text = raw_text[7:]
                    if raw_text.endswith("```"): raw_text = raw_text[:-3]
                    
                    # 成功了！跳出迴圈回傳結果
                    return json.loads(raw_text)
                except Exception as e:
                    last_error = f"解析錯誤: {e}"
                    continue # 內容解析失敗，換下一個

            # --- 情況 B: 額度不足 (429) ---
            elif response.status_code == 429:
                last_error = f"429 Quota Exceeded ({model_name})"
                # 如果是 429，代表模型存在但忙碌，我們可以休息一下重試一次
                time.sleep(2)
                # 這裡不重試同一個了，直接換下一個比較保險
                continue 

            # --- 情況 C: 找不到模型 (404) ---
            elif response.status_code == 404:
                last_error = f"404 Not Found ({model_name})"
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
st.set_page_config(page_title="檢測報告擷取 (精準版號)", layout="wide")
st.title("🧪 通用型第三方檢測報告擷取工具 (精準版號版)")
st.markdown("自動輪詢 gemini-1.5-flash-001/002/8b，確保連線成功。")

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
            
            # 避免瞬間請求過多 (Rate Limit)
            if i > 0: time.sleep(1) 
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.read())
                tmp_path = tmp_file.name

            try:
                md_text = pymupdf4llm.to_markdown(tmp_path)
                if len(md_text) < 50:
                    st.warning(f"⚠️ {uploaded_file.name} 內容過少，跳過。")
                else:
                    # 使用精準版號函式
                    result = analyze_report_final(api_key, md_text, uploaded_file.name)
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
            st.error("❌ 無數據或發生錯誤。")

elif not api_key:
    st.info("請在左側輸入 Google API Key。")
