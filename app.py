import streamlit as st
import pdfplumber
import pandas as pd
import re
import os
import json
from openai import OpenAI

# ---------------------
# Basic setup
# ---------------------
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    st.stop()

client = OpenAI(api_key=api_key)

COLUMNS = [
    "ITEM", "Pb", "Cd", "Hg", "CrVI",
    "PBBs", "PBDEs",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "Cl", "Br", "I", "PFOS", "PFAS",
    "DATE", "FILE"
]

PRIORITY = {"number": 3, "negative": 2, "nd": 1, "none": 0}

# ---------------------
# PDF text extraction
# ---------------------
def extract_text(file):
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            for p in pdf.pages[:10]:
                t = p.extract_text()
                if t:
                    text += t + "\n"
    except:
        return ""
    return text

# ---------------------
# Simple rule parser (NO AI)
# ---------------------
def parse_value(line):
    u = line.upper()
    if "NEGATIVE" in u:
        return {"type": "negative", "value": "NEGATIVE"}
    if "ND" in u:
        return {"type": "nd", "value": "N.D."}
    nums = re.findall(r"\d+(?:\.\d+)?", line)
    nums = [float(n) for n in nums if n not in ["1","2","5","10","100","1000"]]
    if nums:
        return {"type": "number", "value": max(nums)}
    return {"type": "none", "value": ""}

def pick_best(old, new, file):
    if old is None or PRIORITY[new["type"]] > PRIORITY[old["type"]]:
        return {**new, "file": file}
    if new["type"] == "number" and new["value"] > old["value"]:
        return {**new, "file": file}
    return old

# ---------------------
# UI
# ---------------------
st.set_page_config(layout="wide")
st.title("Test Report Tool")

files = st.file_uploader("Upload PDF", type="pdf", accept_multiple_files=True)

if files:
    result = {}
    for f in files:
        text = extract_text(f)
        for line in text.splitlines():
            for key in ["PB", "CD", "HG", "CR(VI)", "DEHP", "BBP", "DBP", "DIBP"]:
                if key in line.upper():
                    r = parse_value(line)
                    result[key] = pick_best(result.get(key), r, f.name)

    row = {"ITEM": "RESULT"}
    for c in COLUMNS:
        row[c] = result.get(c, {}).get("value", "")

    df = pd.DataFrame([row])
    st.dataframe(df, use_container_width=True)
