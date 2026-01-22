import streamlit as st
import os
import json
import re
import pandas as pd
import pymupdf4llm
from typing import Optional
from pydantic import BaseModel, Field
from openai import OpenAI
import tempfile

# ==========================================
# 1. 定義資料結構
# ==========================================
class ReportData(BaseModel):
    # 重金屬
    Pb: Optional[str] = Field(None, description="Lead value.")
    Cd: Optional[str] = Field(None, description="Cadmium value.")
    Hg: Optional[str] = Field(None, description="Mercury value.")
    Cr6: Optional[str] = Field(None, description="Hexavalent Chromium value. Return 'NEGATIVE' if negative.")
    
    # 阻燃劑
    PBBs: Optional[str] = Field(None, description="Sum of PBBs. If all ND, return 'N.D.'.")
    PBDEs: Optional[str] = Field(None, description="Sum of PBDEs. If all ND, return 'N.D.'.")
    
    # 鄰苯二甲酸酯
    DEHP: Optional[str] = Field(None, description="DEHP value.")
    BBP: Optional[str] = Field(None, description="BBP value.")
    DBP: Optional[str] = Field(None, description="DBP value.")
    DIBP: Optional[str] = Field(None, description="DIBP value.")
    
    # 鹵素
    F: Optional[str] = Field(None, description="Fluorine value.")
    Cl: Optional[str] = Field(None, description="Chlorine value.")
    Br: Optional[str] = Field(None, description="Bromine value.")
    I: Optional[str] = Field(None, description="Iodine value.")
    
    # 全氟化合物
    PFOS: Optional[str] = Field(None, description="PFOS value.")
    PFAS_Status: Optional[str] = Field(None, description="Strictly 'REPORT' only if 'PFAS' keyword appears in Test Requested.")
    
    # 日期
    DATE: Optional[str] = Field(None, description="Report Date (YYYY-MM-DD).")

# ==========================================
# 2. 核心功能
# ==========================================
def analyze_report(client, text, filename):
    system_instruction = """
    Extract chemical test data from the Markdown report.
    Rules:
    1. Convert "ND", "N.D.", "<...", "Not Detected" to "N.D.".
    2. If Cr(VI) is "Negative", output "NEGATIVE".
    3. Ignore units.
    4. PBBs/PBDEs: Sum sub-items if numbers exist; if all sub-items are ND, output "N.D.".
    5. PFAS: Set 'PFAS_Status' to "REPORT" ONLY if exact string "PFAS" or "Per- and Polyfluoroalkyl" is in 'Test Requested'. Otherwise null.
    """
    try:
        completion = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Filename: {filename}\n\nReport Content:\n{text}"}
            ],
            response_format=ReportData,
        )
        return completion.choices[0].message.parsed
    except Exception as e:
        st.error(f"解析錯誤 ({filename}): {e}")
        return None

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
    
    final_data = {field: "" for field in ReportData.model_fields.keys()}
    
    for field in final_data.keys():
        if field == "DATE": continue
        best_val = ""
        best_score = -1
        for item in results_list:
            val = getattr(item['data'], field)
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
        pb_score = get_score(data.Pb)
        total_score = sum(get_score(getattr(data, f)) for f in final_data.keys() if f != "DATE")
        
        if pb_score > max_pb_score:
            max_pb_score = pb_score
            max_total_score = total_score
            best_filename = item['filename']
        elif pb_score == max_pb_score:
            if total_score > max_total_score:
                max_total_score = total_score
                best_filename = item['filename']

    main_data = next(r['data'] for r in results_list if r['filename'] == best_filename)
    final_data['DATE'] = main_data.DATE
    
    return final_data, best_filename

# ==========================================
# 3. Streamlit 介面
# ==========================================
st.set_page_config(page_title="檢測報告擷取工具", layout="wide")

st.title("🧪 通用型第三方檢測報告擷取工具 (PDF)")
st.markdown("支援 SGS, CTI, Intertek 等格式，自動擷取數值並整合。")

# 側邊欄：API Key 輸入
with st.sidebar:
    st.header("設定")
    api_key = st.text_input("請輸入 OpenAI API Key", type="password")
    st.markdown("---")
    st.markdown("Created by AI Assistant")

# 檔案上傳區
uploaded_files = st.file_uploader("請上傳 PDF 報告 (可多選)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and api_key:
    if st.button("開始分析", type="primary"):
        client = OpenAI(api_key=api_key)
        processed_files = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, uploaded_file in enumerate(uploaded_files):
            status_text.text(f"正在讀取: {uploaded_file.name} ...")
            
            # 必須將上傳的檔案暫存到硬碟，PyMuPDF4LLM 才能讀取
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.read())
                tmp_path = tmp_file.name

            try:
                # 轉 Markdown
                md_text = pymupdf4llm.to_markdown(tmp_path)
                
                # 簡單判斷是否為掃描檔
                if len(md_text) < 50:
                    st.warning(f"⚠️ {uploaded_file.name} 內容過少，疑似為圖片掃描檔，已跳過。")
                else:
                    # LLM 分析
                    result = analyze_report(client, md_text, uploaded_file.name)
                    if result:
                        processed_files.append({"filename": uploaded_file.name, "data": result})
            finally:
                # 刪除暫存檔
                os.remove(tmp_path)
            
            # 更新進度條
            progress_bar.progress((i + 1) / len(uploaded_files))

        status_text.text("分析完成！")
        
        # 顯示結果
        if processed_files:
            merged_data, primary_filename = merge_results(processed_files)
            
            # 建立表格
            table_row = {
                "ITEM": "1",
                "Pb": merged_data["Pb"],
                "Cd": merged_data["Cd"],
                "Hg": merged_data["Hg"],
                "Cr+6": merged_data["Cr6"],
                "PBBs": merged_data["PBBs"],
                "PBDEs": merged_data["PBDEs"],
                "DEHP": merged_data["DEHP"],
                "BBP": merged_data["BBP"],
                "DBP": merged_data["DBP"],
                "DIBP": merged_data["DIBP"],
                "F": merged_data["F"],
                "Cl": merged_data["Cl"],
                "Br": merged_data["Br"],
                "I": merged_data["I"],
                "PFOS": merged_data["PFOS"],
                "PFAS": merged_data["PFAS_Status"],
                "DATE": merged_data["DATE"],
                "FILE NAME": primary_filename
            }
            
            # 欄位順序
            cols = ["ITEM", "Pb", "Cd", "Hg", "Cr+6", "PBBs", "PBDEs", "DEHP", "BBP", 
                    "DBP", "DIBP", "F", "Cl", "Br", "I", "PFOS", "PFAS", "DATE", "FILE NAME"]
            
            df = pd.DataFrame([table_row])
            df = df[cols] # 重新排序
            
            st.success("✅ 擷取成功")
            st.dataframe(df, hide_index=True)
            
            # 下載按鈕
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 下載 Excel (CSV)",
                data=csv,
                file_name='report_summary.csv',
                mime='text/csv',
            )
        else:
            st.error("❌ 未能提取任何有效數據。")

elif not api_key:
    st.info("請先在左側輸入 API Key。")
