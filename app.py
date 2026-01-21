import streamlit as st
import pdfplumber
import pandas as pd
import re
import os
import json
from openai import OpenAI

# =====================
# 基本設定與 Client 初始化
# =====================

# 優先嘗試從 Streamlit secrets 讀取，若無則從環境變數讀取
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

if not api_key:
    st.error("未設定 OpenAI API Key。請在 Streamlit Secrets 或環境變數中設定。")
    st.stop()

client = OpenAI(api_key=api_key)

ITEMS = [
    "Pb", "Cd", "Hg", "CrVI", "PBBs", "PBDEs",
    "DEHP", "BBP", "DBP", "DIBP",
    "F", "Cl", "Br", "I", "PFOS", "PFAS"
]

# 修正：加入 "report" 類型以避免 PFAS 邏輯報錯
PRIORITY = {"number": 3, "negative": 2, "nd": 1, "report": 1, "none": 0}

# =====================
# PDF 文字擷取
# =====================
def extract_text(file):
    text = ""
    try:
        with pdfplumber.open(file) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t:
                    text += t + "\n"
    except Exception as e:
        raise ValueError(f"PDF 讀取錯誤: {str(e)}")
        
    if not text.strip():
        raise ValueError("無法擷取 PDF 文字 (可能是掃描檔或加密)")
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
# AI 解析 (修正為新版 OpenAI API 語法)
# =====================
def parse_with_ai(text):
    # 限制文字長度以節省 Token (視情況調整)
    truncated_text = text[:3000] 
    
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
- 數值請只回傳數字（不要單位）
- Limit / MDL / RL 不是結果，請忽略

=== 輸出 JSON 格式範例（嚴格遵守）===
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
{truncated_text}
"""

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        response_format={"type": "json_object"} # 強制 JSON 模式 (避免 AI 亂說話)
    )

    content = response.choices[0].message.content
    return json.loads(content)

# =====================
# 正規化與邏輯處理
# =====================
def normalize(val):
    if val is None:
        return {"type": "none", "value": None}
    
    # 處理 dict 類型 (如果是 PFAS report 邏輯傳入)
    if isinstance(val, dict) and val.get("type") == "report":
        return val

    v = str(val).strip().upper()
    if v in ["ND", "N.D.", "NOT DETECTED", "<MDL", "<RL"]:
        return {"type": "nd", "value": "N.D."} # 修正：保留 N.D. 字串以便顯示
    if v == "NEGATIVE":
        return {"type": "negative", "value": "NEGATIVE"}
    try:
        # 移除可能的單位或是非數字字符
        clean_v = re.sub(r"[^\d\.]", "", v)
        return {"type": "number", "value": float(clean_v)}
    except:
        return {"type": "none", "value": None}

def sum_items(items):
    total = 0.0
    has_value = False
    for i in items:
        try:
            val_str = str(i["value"]).upper()
            if "N.D." in val_str or "ND" in val_str:
                continue
            # 清理非數字
            clean_val = re.sub(r"[^\d\.]", "", val_str)
            total += float(clean_val)
            has_value = True
        except:
            continue
    return total if has_value else "N.D."

def pick_best(old, new):
    if old is None:
        return new
    
    # 安全檢查：確保 type 存在於 PRIORITY 中
    old_p = PRIORITY.get(old["type"], 0)
    new_p = PRIORITY.get(new["type"], 0)
    
    if new_p > old_p:
        return new
    if new["type"] == "number" and old["type"] == "number":
        # 取大值
        if new["value"] > old["value"]:
            return new
    return old

# =====================
# Streamlit UI
# =====================
st.set_page_config(page_title="RoHS / PFAS Parser", layout="wide")
st.title("第三方檢測報告自動彙總系統")

files = st.file_uploader("上傳 PDF（可多選）", type="pdf", accept_multiple_files=True)

if files:
    results = {i: None for i in ITEMS}
    pb_source = ""
    date_result = ""
    errors = []

    with st.status("正在分析報告...", expanded=True) as status:
        for f in files:
            st.write(f"正在處理: {f.name}")
            try:
                text = extract_text(f)
                
                # 嘗試擷取日期
                date = extract_date(text)
                if date and not date_result:
                    date_result = date

                # AI 解析
                ai = parse_with_ai(text)

                # 1. 一般項目處理
                for k, v in ai.get("items", {}).items():
                    if k in ITEMS:
                        norm = normalize(v)
                        norm["file"] = f.name
                        results[k] = pick_best(results[k], norm)

                # 2. PBBs / PBDEs 加總處理
                pbb_sum = sum_items(ai.get("pbb_items", []))
                pbde_sum = sum_items(ai.get("pbde_items", []))

                norm_pbb = normalize(pbb_sum)
                norm_pbb["file"] = f.name
                results["PBBs"] = pick_best(results["PBBs"], norm_pbb)

                norm_pbde = normalize(pbde_sum)
                norm_pbde["file"] = f.name
                results["PBDEs"] = pick_best(results["PBDEs"], norm_pbde)

                # 3. PFAS 特別處理
                if ai.get("pfas"):
                    pfas_res = {"type": "report", "value": "REPORT", "file": f.name}
                    results["PFAS"] = pick_best(results["PFAS"], pfas_res)

            except Exception as e:
                errors.append({"檔案": f.name, "錯誤原因": str(e)})
                st.error(f"{f.name} 發生錯誤: {e}")

        status.update(label="分析完成", state="complete", expanded=False)

    # 擷取 Pb 來源檔案名稱
    if results["Pb"] and results["Pb"].get("file"):
        pb_source = results["Pb"]["file"]

    # =====================
    # 顯示結果
    # =====================
    st.subheader("彙總結果")

    # 建立顯示用的 Dict
    row = {}
    for i in ITEMS:
        res = results[i]
        if res and res["value"] is not None:
            row[i] = res["value"]
        else:
            row[i] = ""
            
    row["DATE"] = date_result
    row["檔案名稱"] = pb_source

    st.dataframe(pd.DataFrame([row]), use_container_width=True)

    if errors:
        st.subheader("⚠️ 解析失敗的檔案")
        st.dataframe(pd.DataFrame(errors), use_container_width=True)
