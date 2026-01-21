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
    st.error("❌ 未偵測到 API Key，請在 Streamlit Secrets 設定 OPENAI_API_KEY")
    st.stop()

client = OpenAI(api_key=api_key)

ITEMS_ORDER = [
    "ITEM", "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "Cl", "Br", "I", "PFOS", "PFAS",
    "DATE", "檔案名稱"
]

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
# 2. PDF 文字擷取
# =====================
def extract_text(file):
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            for i, p in enumerate(pdf.pages[:20]):
                t = p.extract_text(layout=True, x_tolerance=2)
                if t:
                    text += f"\n--- Page {i+1} ---\n{t}"
    except:
        return ""
    return text

# =====================
# 3. 日期擷取
# =====================
def extract_date(text):
    patterns = [
        r"\b20\d{2}[-/]\d{2}[-/]\d{2}\b",
        r"\b\d{2}[-/]\d{2}[-/]20\d{2}\b",
        r"\b\d{2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+20\d{2}\b"
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group()
    return None

# =====================
# 4. AI：只找「可能的結果行」
# =====================
def parse_with_ai(text):
    prompt = f"""
請從以下第三方檢測報告中，找出「可能包含檢測結果的整行文字」。

=== 項目 ===
Pb, Cd, Hg, CrVI,
DEHP, BBP, DBP, DIBP,
F, Cl, Br, I, PFOS,
PBBs, PBDEs

=== 規則 ===
- 回傳完整行文字
- 同一項目可多行
- 不要回 Limit / MDL 說明
- 不要判斷數值

=== JSON ===
{{
  "Pb": ["Lead ... ND"],
  "PBBs": ["DecaBDE ... 0.12", "OctaBDE ... ND"],
  "PFAS_requested": true
}}

=== 內容 ===
{text[:16000]}
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# =====================
# 5. Python 規則判讀
# =====================
def extract_result_from_line(line):
    u = line.upper()

    if "NEGATIVE" in u:
        return {"type": "negative", "value": "NEGATIVE"}

    if "N.D" in u or re.search(r"\bND\b", u):
        return {"type": "nd", "value": "N.D."}

    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", line)]
    nums = [n for n in nums if n not in [1, 2, 5, 10, 100, 1000]]

    if nums:
        return {"type": "number", "value": max(nums)}

    return {"type": "none", "value": None}

def compare_and_pick_best(current, new, file):
    if current is None:
        return {**new, "file": file}

    if PRIORITY_MAP[new["type"]] > PRIORITY_MAP[current["type"]]:
        return {**new, "file": file}

    if new["type"] == "number" and new["value"] > current["value"]:
        return {**new, "file": file}

    return current

# =====================
# 6. Streamlit UI
# =====================
st.set_page_config(page_title="檢測報告彙總系統", layout="wide")
st.title("🧪 第三方檢測報告自動彙總系統")

uploaded_files = st.file_uploader("上傳 PDF", type="pdf", accept_multiple_files=True)

if uploaded_files:
    final_results = {}
    errors = []

    for f in uploaded_files:
        try:
            text = extract_text(f)
            if not text:
                raise ValueError("無法讀取 PDF")

            ai_data = parse_with_ai(text)

            for chem, lines in ai_data.items():
                if chem == "PFAS_requested":
                    val = {"type": "report", "value": "REPORT"} if lines else {"type": "nd", "value": "N.D."}
                    final_results["PFAS"] = compare_and_pick_best(final_results.get("PFAS"), val, f.name)
                    continue

                if not isinstance(lines, list):
                    continue

                if chem in ["PBBs", "PBDEs"]:
                    nums = []
                    for l in lines:
                        r = extract_result_from_line(l)
                        if r["type"] == "number":
                            nums.append(r["value"])
                    val = {"type": "number", "value": sum(nums)} if nums else {"type": "nd", "value": "N.D."}
                    final_results[chem] = compare_and_pick_best(final_results.get(chem), val, f.name)

                elif chem in CHEMICAL_ITEMS:
                    for l in lines:
                        r = extract_result_from_line(l)
                        final_results[chem] = compare_and_pick_best(final_results.get(chem), r, f.name)

            date = extract_date(text)
            if date and "DATE" not in final_results:
                final_results["DATE"] = {"value": date}

        except Exception as e:
            errors.append({"檔案": f.name, "錯誤": str(e)})

    row = {"ITEM": "RESULT"}
    for k in ITEMS_ORDER:
        if k in final_results and "value" in final_results[k]:
            row[k] = final_results[k]["value"]
        else:
            row[k] = ""

    df = pd.DataFrame([row])
    st.dataframe(df, use_container_width=True)

    if errors:
        st.warning("⚠️ 以下檔案解析失敗")
        st.dataframe(pd.DataFrame(errors))
