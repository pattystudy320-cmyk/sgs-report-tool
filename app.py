import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 關鍵字與黑名單定義 ---

# 數值黑名單：過濾 Limit, MDL, 年份, 標準編號
# 注意：這些是用來判斷「這不是結果」，過濾時會轉成 float 比對
VALUE_BLACKLIST = [
    1000.0, 100.0, 50.0, 25.0, 20.0, 10.0, 8.0, 5.0, 2.0, 0.1, 0.01, # Limits & MDLs
    2011.0, 2015.0, 2016.0, 2017.0, 2023.0, 2024.0, 2025.0, # Years
    62321.0, 3052.0, 14582.0, 3540.0, 17681.0, 18219.0, 15968.0, # Method Numbers
    1.0 # 避免抓到版本號等
]

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

PFAS_KEYWORDS = [
    "Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物質"
]

# Intertek 專用 PBB/PBDE 子項目 (避開 Limit 表大標題)
INTERTEK_SUB_KEYWORDS = [
    "monobrominated", "dibrominated", "tribrominated", "tetrabrominated", 
    "pentabrominated", "hexabrominated", "heptabrominated", "octabrominated", 
    "nonabrominated", "decabrominated", "monobb", "monobde"
]

SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "铅", "Pb", "납"], 
    "Cd": ["Cadmium", "镉", "Cd", "카드뮴"], 
    "Hg": ["Mercury", "汞", "Hg", "수은"], 
    "Cr6+": ["Hexavalent Chromium", "六价铬", "六價鉻", "Cr(VI)", "Chromium VI", "Cr6+", "6가 크롬"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["Perfluorooctane sulfonates", "PFOS"],
    "F": ["Fluorine", "氟", "(F)"],
    "CL": ["Chlorine", "氯", "(Cl)"],
    "BR": ["Bromine", "溴", "(Br)"],
    "I": ["Iodine", "碘", "(I)"]
}

GROUP_KEYWORDS = {
    "PBB": ["Polybrominated Biphenyls", "PBBs"],
    "PBDE": ["Polybrominated Diphenyl Ethers", "PBDEs"]
}

# --- 2. 輔助函式 ---

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def extract_date_from_text(text):
    text = clean_text(text)
    patterns = [
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", 
        r"Date\s*[:\.]?\s*(0?[1-9]|[12][0-9]|3[01])\s+([a-zA-Z]{3})\s+(20\d{2})", 
        r"(0?[1-9]|[12][0-9]|3[01])\s*[-/]\s*([a-zA-Z]{3})\s*[-/]\s*(20\d{2})", 
        r"([a-zA-Z]{3})\.?\s+(0?[1-9]|[12][0-9]|3[01])[,\s]+\s*(20\d{2})", 
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])",
        r"(20\d{2})-(0?[1-9]|1[0-2])-(0?[1-9]|[12][0-9]|3[01])"
    ]
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                full_match = match.group(0)
                if "年" in full_match:
                    groups = match.groups()
                    dt = datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                elif "-" in full_match and full_match[0] == "2":
                     dt = datetime.strptime(full_match, "%Y-%m-%d")
                else:
                    if "date" in full_match.lower():
                        full_match = re.sub(r"Date\s*[:\.]?\s*", "", full_match, flags=re.IGNORECASE)
                    clean_str = re.sub(r"[,./-]", " ", full_match) 
                    clean_str = " ".join(clean_str.split())
                    for fmt in ["%d %b %Y", "%Y %m %d", "%b %d %Y", "%B %d %Y"]:
                        try:
                            dt = datetime.strptime(clean_str, fmt)
                            break
                        except: continue
                if dt and 2000 <= dt.year <= 2030: 
                    found_dates.append(dt)
            except: continue
    if found_dates: return max(found_dates)
    return None

def identify_company(text):
    txt = text.lower()
    if "sgs" in txt: return "SGS"
    if "urhongxin" in txt: return "URHONGXIN"
    if "intertek" in txt: return "INTERTEK"
    if "cti" in txt: return "CTI"
    if "tuv" in txt: return "TUV"
    return "OTHERS"

# --- 3. Intertek 專用解析模組 (v70.0) ---

def scan_row_for_value(row_cells):
    """
    Intertek 行內直讀邏輯 - v70.0 改良版
    優先權：數字(非Limit) > Negative > N.D.
    輸出：保留原始字串 (不轉 float 格式)
    """
    candidates_num = []
    has_negative = False
    has_nd = False

    for cell in row_cells:
        txt = clean_text(cell)
        if not txt: continue
        txt_lower = txt.lower()

        # 1. 排除雜訊
        if any(x in txt_lower for x in ["mg/kg", "ppm", "µg", "%", "iec", "epa", "iso", "method", "reference", "limit", "mdl", "loq"]):
            continue

        # 2. 偵測 Negative
        if "negative" in txt_lower:
            has_negative = True
            continue

        # 3. 偵測 ND
        if "nd" in txt_lower or "n.d." in txt_lower or "not detected" in txt_lower:
            has_nd = True
            continue

        # 4. 偵測數字
        clean_num_str = re.sub(r"[<>]", "", txt).strip()
        try:
            val = float(clean_num_str)
            # 黑名單過濾
            if val not in VALUE_BLACKLIST:
                # 儲存 (原始字串, 數值大小)
                candidates_num.append((txt, val))
        except:
            pass

    # 決策邏輯 (User Requested: Number > Negative > ND)
    if candidates_num:
        # 取數值最大的那個 (例如同時抓到 1381 和 0.1，取 1381)
        best_match = sorted(candidates_num, key=lambda x: x[1], reverse=True)[0]
        return best_match[0] # 回傳原始字串 (如 "1381")
    
    if has_negative:
        return "Negative"
        
    if has_nd:
        return "N.D."

    return None

def process_intertek(pdf, filename, data_pool, debug_logs):
    TARGET_MAP = [
        ("Pb", ["lead", "pb"]),
        ("Cd", ["cadmium", "cd"]),
        ("Hg", ["mercury", "hg"]),
        ("Cr6+", ["hexavalent chromium", "cr(vi)", "cr6+"]),
        ("DEHP", ["di(2-ethylhexyl) phthalate", "dehp"]),
        ("BBP", ["butyl benzyl phthalate", "bbp"]),
        ("DBP", ["dibutyl phthalate", "dbp"]),
        ("DIBP", ["diisobutyl phthalate", "dibp"]),
        ("PFOS", ["perfluorooctane sulfonates", "pfos"]),
        ("F", ["fluorine", "(f)"]),
        ("CL", ["chlorine", "(cl)"]),
        ("BR", ["bromine", "(br)"]),
        ("I", ["iodine", "(i)"]),
        ("PBB", INTERTEK_SUB_KEYWORDS + ["monobb"]),
        ("PBDE", INTERTEK_SUB_KEYWORDS + ["monobde"]),
    ]

    full_text_content = ""
    # 1. 全文掃描 PFAS
    for page in pdf.pages:
        text = page.extract_text() or ""
        full_text_content += text
    
    # PFAS REPORT 判定
    if any(kw.lower() in full_text_content.lower() for kw in PFAS_KEYWORDS):
        if not data_pool["PFAS"]:
            data_pool["PFAS"].append({"priority": (5, 0, "REPORT"), "filename": filename})

    # 2. 表格掃描
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            # --- v70.0 關鍵修正：嚴格過濾無效表格 ---
            # 取得表頭文字 (前 3 行合併)
            header_rows = table[:3]
            header_str = " ".join([str(c) for row in header_rows for c in row if c]).lower()
            
            # 過濾 1: Limit 表
            if "restricted substances" in header_str and "limits" in header_str: continue
            # 過濾 2: 樣品描述表 (造成 BBP 抓到 RM20 的元兇)
            if "sample description" in header_str or "product name" in header_str or "item no" in header_str: continue
            # 過濾 3: 附錄表 (造成 PFOS 抓到 CAS No 的元兇)
            if "cas no" in header_str and "name" in header_str: continue
            # --------------------------------------------

            for row in table:
                clean_row = [clean_text(cell) for cell in row if cell]
                if not clean_row: continue
                row_text = " ".join(clean_row).lower()

                for target, keywords in TARGET_MAP:
                    hit = False
                    for kw in keywords:
                        if kw in row_text:
                            # 避免誤判 (如 Pb 在 PBB 描述中)
                            if target in ["Pb", "Cd", "Hg"] and ("poly" in row_text or "pbb" in row_text):
                                continue
                            hit = True
                            break
                    
                    if hit:
                        val = scan_row_for_value(clean_row)
                        if val:
                            # 計算優先分數
                            if val.lower() == "negative": priority_score = 4
                            elif "nd" in val.lower(): priority_score = 1
                            else: priority_score = 5 # 數字優先級最高
                            
                            # 為了排序，還是要算一個數值 (如果是文字則為 0)
                            try:
                                real_val_num = float(re.sub(r"[<>]", "", val))
                            except:
                                real_val_num = 0

                            data_pool[target].append({
                                "priority": (priority_score, real_val_num, val),
                                "filename": filename
                            })

# --- 4. 通用/SGS/CTI 解析模組 (保留完整功能) ---

def parse_sgs_cti_generic(pdf, filename, company, data_pool, debug_logs):
    # PFAS 檢查
    full_text = ""
    for p in pdf.pages: full_text += (p.extract_text() or "")
    if any(k.lower() in full_text.lower() for k in PFAS_KEYWORDS):
         if not data_pool["PFAS"]:
             data_pool["PFAS"].append({"priority": (5, 0, "REPORT"), "filename": filename})

    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            # 判斷表頭
            header_row_idx = -1
            result_col_idx = -1
            
            for r_idx, row in enumerate(table[:5]):
                for c_idx, cell in enumerate(row):
                    txt = clean_text(cell).lower()
                    if "result" in txt or "結果" in txt:
                        header_row_idx = r_idx
                        result_col_idx = c_idx
                        break
                if header_row_idx != -1: break
            
            if result_col_idx == -1 and len(table[0]) > 1:
                result_col_idx = len(table[0]) - 1

            for r_idx in range(header_row_idx + 1, len(table)):
                row = table[r_idx]
                if len(row) <= result_col_idx: continue
                
                item_name = clean_text(row[0]) 
                res_val = clean_text(row[result_col_idx])
                
                # 簡單項目
                for target, kws in SIMPLE_KEYWORDS.items():
                    if any(k in item_name for k in kws) or any(k.lower() in item_name.lower() for k in kws):
                        if "nd" in res_val.lower():
                            data_pool[target].append({"priority": (1, 0, "N.D."), "filename": filename})
                        else:
                            try:
                                num = float(re.sub(r"[<>]", "", res_val))
                                if num not in VALUE_BLACKLIST:
                                    data_pool[target].append({"priority": (3, num, res_val), "filename": filename})
                            except: pass
                
                # 群組項目 (SGS/CTI 需要抓大標題行，如 Sum of PBBs)
                for group, kws in GROUP_KEYWORDS.items():
                    if any(k in item_name for k in kws):
                        if "nd" in res_val.lower():
                             data_pool[group].append({"priority": (1, 0, "N.D."), "filename": filename})

# --- 5. Main Processing ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS}
    all_dates = []
    debug_logs = []
    
    for file in files:
        filename = file.name
        try:
            with pdfplumber.open(file) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
                company = identify_company(first_page_text)
                
                for i in range(min(3, len(pdf.pages))):
                    d = extract_date_from_text(pdf.pages[i].extract_text())
                    if d: 
                        all_dates.append((d, filename))
                        break
                
                if company == "INTERTEK":
                    process_intertek(pdf, filename, data_pool, debug_logs)
                else:
                    parse_sgs_cti_generic(pdf, filename, company, data_pool, debug_logs)

        except Exception as e:
            st.error(f"Error processing {filename}: {e}")

    final_row = {}
    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = ""
        else:
            # 排序：優先級(5>4>3>1) -> 數值大 -> 來源
            best = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
            final_row[key] = best['priority'][2]

    if all_dates:
        best_date = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_row["日期"] = best_date[0].strftime("%Y/%m/%d")
        final_row["檔案名稱"] = best_date[1]
    else:
        final_row["檔案名稱"] = files[0].name if files else ""

    return [final_row], debug_logs

# --- Main UI ---

if __name__ == "__main__":
    st.set_page_config(page_title="SGS/CTI/Intertek Tool v70.0", layout="wide")
    st.title("📄 萬用型檢測報告聚合工具 (v70.0 Intertek 最終修正版)")
    st.info("💡 v70.0：針對 Intertek 全面修正：1. 嚴格封殺 Sample Description/Annex 等表格，解決 BBP/PFOS 抓錯問題。 2. Cr6+ 加入 Negative 優先偵測。 3. 輸出結果保留原始格式 (不自動加 .0)。")

    uploaded_files = st.file_uploader("請選取 PDF 檔案", type="pdf", accept_multiple_files=True)

    if uploaded_files:
        if st.button("🔄 開始分析"):
            result_data, debug_logs = process_files(uploaded_files)
            df = pd.DataFrame(result_data)
            for col in OUTPUT_COLUMNS:
                if col not in df.columns: df[col] = ""
            df = df[OUTPUT_COLUMNS]
            
            st.success("分析完成")
            st.dataframe(df)
            
            with st.expander("Debug Logs"):
                st.write(debug_logs)
