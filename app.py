import streamlit as st
import pdfplumber
import pandas as pd
import re
from datetime import datetime

# ======================
# 基本設定
# ======================
ITEMS = [
    "Pb","Cd","Hg","CrVI","PBBs","PBDEs",
    "DEHP","BBP","DBP","DIBP",
    "F","CL","BR","I","PFOS","PFAS"
]

PRIORITY = {
    "number": 3,
    "negative": 2,
    "nd": 1,
    "none": 0
}

# ======================
# 工具函式
# ======================
def extract_text(pdf_file):
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    if not text.strip():
        raise ValueError("PDF 無法擷取文字")
    return text


def detect_date(text):
    patterns = [
        r"\b\d{4}[-/]\d{2}[-/]\d{2}\b",
        r"\b\d{2}[-/]\d{2}[-/]\d{4}\b"
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group()
    return None


def classify_value(raw):
    raw = raw.upper()
    if raw in ["ND", "N.D.", "NOT DETECTED"]:
        return "nd", "N.D."
    if raw == "NEGATIVE":
        return "negative", "NEGATIVE"
    try:
        return "number", float(raw)
    except:
        return "none", None


# ⚠️ 這裡是「未來接 AI 的位置」
def parse_report_with_ai(text):
    """
    未來由 AI 回傳格式：
    {
      "Pb": {"type":"number","value":20},
      ...
      "PFAS":"REPORT"
    }
    """
    raise NotImplementedError("尚未接 AI")


# ======================
# 彙總邏輯
# ======================
def pick_best(existing, new):
    if existing is None:
        return new
    if PRIORITY[new["type"]] > PRIORITY[existing["type"]]:
        return new
    if new["type"] == "number" and new["value"] > existing["value"]:
        return new
    return existing


# ======================
# Streamlit UI
# ======================
st.set_page_config(page_title="RoHS / PFAS Report Parser", layout="wide")
st.title("檢測報告自動彙總工具")

uploaded_files = st.file_uploader(
    "請上傳檢測報告 PDF（可多選）",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:
    results = {item: None for item in ITEMS}
    date_result = None
    pb_source_file = None
    error_files = []

    for file in uploaded_files:
        try:
            text = extract_text(file)

            # DATE
            date = detect_date(text)
            if date and not date_result:
                date_result = date

            # ⚠️ 這裡之後會換成 AI
            raise NotImplementedError("尚未實作 AI 解析")

        except Exception as e:
            error_files.append({
                "file": file.name,
                "error": str(e)
            })

    # ======================
    # 顯示結果
    # ======================
    st.subheader("彙總結果")

    data = {
        "ITEM": ["RESULT"],
    }

    for item in ITEMS:
        data[item] = [results[item]["value"] if results[item] else ""]

    data["DATE"] = [date_result]
    data["檔案名稱"] = [pb_source_file]

    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)

    # ======================
    # 錯誤檔案顯示
    # ======================
    if error_files:
        st.subheader("⚠️ 解析失敗的檔案")
        err_df = pd.DataFrame(error_files)
        st.dataframe(err_df, use_container_width=True)
