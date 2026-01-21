import os
import streamlit as st
import pdfplumber
import pandas as pd
import re
import json
from openai import OpenAI

# =====================
# Basic setup
# =====================
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    st.error("OPENAI_API_KEY not found in secrets")
    st.stop()

client = OpenAI(api_key=api_key)

ITEMS_ORDER = [
    "ITEM", "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "Cl", "Br", "I", "PFOS", "PFAS",
    "DATE", "FILE"
]

CHEMICAL_ITEMS = [
    "Pb", "Cd", "Hg", "CrVI",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "Cl", "Br", "I", "PFOS"
]

PRIORITY_MAP = {"number":3,"negative":2,"report":2,"nd":1,"none":0}

# =====================
# PDF text extraction
# =====================
def extract_text(file):
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            for p in pdf.pages[:20]:
                t = p.extract_text()
                if t:
                    text += t + "\n"
    except:
        return ""
    return text

# =====================
# Date extraction
# =====================
def extract_date(text):
    m = re.search(r"\b20\d{2}[-/]\d{2}[-/]\d{2}\b", text)
    return m.group() if m else ""

# =====================
# AI: find result lines only
# =====================
def parse_with_ai(text):
    prompt = f"""
Find result lines only. Do not calculate values.

Items:
Pb, Cd, Hg, CrVI, DEHP, BBP, DBP, DIBP,
F, Cl, Br, I, PFOS, PBBs, PBDEs

JSON only.
{text[:16000]}
"""
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0,
        response_format={"type":"json_object"}
    )
    return json.loads(r.choices[0].message.content)

# =====================
# Rule-based parsing
# =====================
def extract_result_from_line(line):
    u = line.upper()
    if "NEGATIVE" in u:
        return {"type":"negative","value":"NEGATIVE"}
    if "ND" in u:
        return {"type":"nd","value":"N.D."}
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", line)]
    nums = [n for n in nums if n not in [1,2,5,10,100,1000]]
    if nums:
        return {"type":"number","value":max(nums)}
    return {"type":"none","value":""}

def pick_best(cur, new, file):
    if cur is None or PRIORITY_MAP[new["type"]] > PRIORITY_MAP[cur["type"]]:
        return {**new,"file":file}
    if new["type"]=="number" and new["value"]>cur["value"]:
        return {**new,"file":file}
    return cur

# =====================
# UI
# =====================
st.set_page_config(layout="wide")
st.title("Test Report Aggregation Tool")

files = st.file_uploader("Upload PDF files", type="pdf", accept_multiple_files=True)

if files:
    final = {}
    errors = []

    for f in files:
        try:
            text = extract_text(f)
            data = parse_with_ai(text)

            for chem, lines in data.items():
                if not isinstance(lines, list):
                    continue
                for l in lines:
                    r = extract_result_from_line(l)
                    final[chem] = pick_best(final.get(chem), r, f.name)

            d = extract_date(text)
            if d and "DATE" not in final:
                final["DATE"] = {"value": d}

        except Exception as e:
            errors.append({"file":f.name,"error":str(e)})

    row = {"ITEM":"RESULT"}
    for k in ITEMS_ORDER:
        row[k] = final.get(k,{}).get("value","")

    st.dataframe(pd.DataFrame([row]), use_container_width=True)

    if errors:
        st.warning("Some files failed")
        st.dataframe(pd.DataFrame(errors))
