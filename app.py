import streamlit as st
import pdfplumber
import pandas as pd
import re
import os
import json
import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")


# =====================
# OpenAI Client
# =====================
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =====================
# 基本設定
# =====================
ITEMS = [
    "Pb","Cd","Hg","CrVI","PBBs","PBDEs",
    "DEHP","BBP","DBP","DIBP",
    "F","CL","BR","I","PFOS","PFAS"
]

PRIORITY = {"number":3, "negative":2, "nd":1, "none":0}

# =====================
# PDF 文字擷取
# =====================
def extract_text(file):
    text = ""
    with pdfplumber.open(file) as pdf:
        for p in pdf.pages:
            t = p.extract_text()
            if t:
                text += t + "\n"
    if not text.strip():
        raise ValueError("無法擷取 PDF 文字")
    return text

# =====================
# DATE 擷取
# =====================
def extract_date(text):
    patterns = [
        r"\b\d{4}[-/]\d{2}[-/]\d{2}\b",
        r"\b\d{2}[-/]\d{2}[-/]\d{4}\b"
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group()
    return None

# =====================
# AI 解析
# =====================
import openai
import os
import json

openai.api_key = os.getenv("OPENAI_API_KEY")

def parse_with_ai(text):
    prompt = f"""
你是一位第三方檢測實驗室的資深工程師。

請解析以下檢測報告內容（不同實驗室格式可能不同），
請用「語意」判斷，不要依賴欄位名稱。

=== 任務 ===
擷取以下項目的實際測試結果：
Pb, Cd, Hg, CrVI,
DEHP, BBP, DBP, DIBP,
F, Cl, Br, I,
PFOS

並判斷：
1. 是否有 PFAS 檢測（只要有即為 true）
2. 所有屬於 PBBs 的子項目與結果
3. 所有屬於 PBDEs 的子項目與結果

=== 規則 ===
- 結果若為 ND / N.D. / Not Detected / < MDL → "N.D."
- NEGATIVE → "NEGATIVE"
- 數值請只回傳數字
- Limit / MDL / RL 不是結果

=== 輸出 JSON（只輸出 JSON）===
{{
  "items": {{
    "Pb": "N.D.",
    "Cd": "0.002"
  }},
  "pbb_items": [
    {{"name":"DecaBDE","value":"0.1"}}
  ],
  "pbde_items": [
    {{"name":"PentaBDE","value":"0.05"}}
  ],
  "pfas": true
}}

=== 報告內容 ===
{text}
"""

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )

    return json.loads(response.choices[0].message["content"])

# =====================
# 正規化
# =====================
def normalize(val):
    if val is None:
        return {"type":"none","value":None}
    v = str(val).upper()
    if v in ["ND","N.D.","NOT DETECTED"]:
        return {"type":"nd","value":None}
    if v == "NEGATIVE":
        return {"type":"negative","value":None}
    try:
        return {"type":"number","value":float(v)}
    except:
        return {"type":"none","value":None}

def sum_items(items):
    total = 0.0
    for i in items:
        try:
            total += float(i["value"])
        except:
            continue
    return total if total > 0 else "N.D."

def pick_best(old, new):
    if old is None:
        return new
    if PRIORITY[new["type"]] > PRIORITY[old["type"]]:
        return new
    if new["type"]=="number" and new["value"]>old["value"]:
        return new
    return old

# =====================
# Streamlit UI
# =====================
st.set_page_config(page_title="RoHS / PFAS Parser", layout="wide")
st.title("第三方檢測報告自動彙總系統")

files = st.file_uploader("上傳 PDF（可多選）", type="pdf", accept_multiple_files=True)

if files:
    results = {i:None for i in ITEMS}
    pb_source = None
    date_result = None
    errors = []

    for f in files:
        try:
            text = extract_text(f)
            date = extract_date(text)
            if date and not date_result:
                date_result = date

            ai = parse_with_ai(text)

            # 一般項目
            for k,v in ai["items"].items():
                norm = normalize(v)
                norm["file"] = f.name
                results[k] = pick_best(results[k], norm)

            # PBBs / PBDEs
            pbb_sum = sum_items(ai.get("pbb_items",[]))
            pbde_sum = sum_items(ai.get("pbde_items",[]))

            results["PBBs"] = pick_best(results["PBBs"], normalize(pbb_sum))
            results["PBDEs"] = pick_best(results["PBDEs"], normalize(pbde_sum))

            # PFAS
            if ai.get("pfas"):
                results["PFAS"] = {"type":"report","value":"REPORT","file":f.name}

        except Exception as e:
            errors.append({"檔案":f.name,"錯誤原因":str(e)})

    if results["Pb"] and results["Pb"]["type"]=="number":
        pb_source = results["Pb"]["file"]

    # =====================
    # 顯示結果
    # =====================
    st.subheader("彙總結果")

    row = {"ITEM":"RESULT"}
    for i in ITEMS:
        row[i] = results[i]["value"] if results[i] else ""
    row["DATE"] = date_result
    row["檔案名稱"] = pb_source

    st.dataframe(pd.DataFrame([row]), use_container_width=True)

    if errors:
        st.subheader("⚠️ 解析失敗的檔案")
        st.dataframe(pd.DataFrame(errors), use_container_width=True)


