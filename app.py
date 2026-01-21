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
# 嘗試讀取 API Key
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

PRIORITY = {"number": 3, "negative": 2, "nd": 1, "report": 1, "none": 0}

# =====================
# PDF 文字擷取 (安全版)
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
        # 這裡不丟出錯誤，而是回傳空字串讓後續處理，避免直接崩潰
        return ""
        
    if not text.strip():
        # 如果抓不到字，回傳空字串
        return ""

    # [關鍵]：強制濾掉亂碼，確保回傳的是乾淨的 UTF-8 字串
    return text.encode("utf-8", "ignore").decode("utf-8")

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
def parse_with_ai(text):
    if not text:
        return {}

    # 限制文字長度
    truncated_text = text[:3500] 
    
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

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception:
        return {}

# =====================
# 正規化與邏輯處理
# =====================
def normalize(val):
    if val is None:
        return {"type": "none", "value": None}
    
    if isinstance(val, dict) and val.get("type") == "report":
        return val

    v = str(val).strip().upper()
    if v in ["ND", "N.D.", "NOT DETECTED", "<MDL", "<RL"]:
        return {"type": "nd", "value": "N.D."}
    if v == "NEGATIVE":
        return {"type": "negative", "value": "NEGATIVE"}
    try:
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
            clean_val = re.sub(r"[^\d\.]", "", val_str)
            total += float(clean_val)
            has_value = True
        except:
            continue
    return total if has_value else "N.D."

def pick_best(old, new):
    if old is None:
        return new
    
    old_p = PRIORITY.get(old["type"], 0)
    new_p = PRIORITY.get(new["type"], 0)
    
    if new_p > old_p:
        return new
    if new["type"] == "number" and old["type"] == "number":
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

    # 使用 Status 容器顯示進度
    with st.status("正在分析報告...", expanded=True) as status:
        for f in files:
            # 1. 檔名清洗：這是最常出錯的地方，我們強制把它轉成安全字串
            # 如果檔名有無法編碼的字，就用 'unknown_file' 代替，防止系統崩潰
            try:
                safe_filename = f.name.encode("utf-8", "ignore").decode("utf-8")
            except:
                safe_filename = "unknown_filename.pdf"
                
            st.write(f"正在處理: {safe_filename}")
            
            try:
                # 2. 讀取文字
                text = extract_text(f)
                
                if not text:
                    raise ValueError("無法讀取 PDF 文字 (可能是圖片掃描檔)")

                # 3. 擷取日期
                date = extract_date(text)
                if date and not date_result:
                    date_result = date

                # 4. AI 解析
                ai = parse_with_ai(text)
                if not ai:
                    raise ValueError("AI 無法解析內容")

                # 5. 彙整結果
                # 一般項目
                for k, v in ai.get("items", {}).items():
                    if k in ITEMS:
                        norm = normalize(v)
                        norm["file"] = safe_filename
                        results[k] = pick_best(results[k], norm)

                # PBBs / PBDEs 加總
                pbb_sum = sum_items(ai.get("pbb_items", []))
                pbde_sum = sum_items(ai.get("pbde_items", []))

                norm_pbb = normalize(pbb_sum)
                norm_pbb["file"] = safe_filename
                results["PBBs"] = pick_best(results["PBBs"], norm_pbb)

                norm_pbde = normalize(pbde_sum)
                norm_pbde["file"] = safe_filename
                results["PBDEs"] = pick_best(results["PBDEs"], norm_pbde)

                # PFAS
                if ai.get("pfas"):
                    pfas_res = {"type": "report", "value": "REPORT", "file": safe_filename}
                    results["PFAS"] = pick_best(results["PFAS"], pfas_res)

            except Exception as e:
                # 這裡也要防呆，確保錯誤訊息不會包含亂碼
                try:
                    error_msg = str(e)
                except:
                    error_msg = "未知錯誤 (編碼異常)"
                
                errors.append({"檔案": safe_filename, "錯誤原因": error_msg})
                # 注意：這裡刪除了 print()，避免後台編碼錯誤

        status.update(label="分析完成", state="complete", expanded=False)

    if results["Pb"] and results["Pb"].get("file"):
        pb_source = results["Pb"]["file"]

    # =====================
    # 顯示結果
    # =====================
    st.subheader("彙總結果")

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
