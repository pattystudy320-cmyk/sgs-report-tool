import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 關鍵字與黑名單定義 ---

# 數值黑名單：過濾 Limit, MDL, 年份, 標準編號
# 新增 0.5 (韓國報告 Cd MDL)
VALUE_BLACKLIST = [
    1000.0, 100.0, 50.0, 25.0, 20.0, 10.0, 8.0, 5.0, 2.0, 1.0, 
    0.5, 0.1, 0.05, 0.01, # MDLs and Limits
    2011.0, 2015.0, 2016.0, 2017.0, 2023.0, 2024.0, 2025.0, # Years
    62321.0, 3052.0, 14582.0, 3540.0, 17681.0, 18219.0, 15968.0, 111.0 # Method Numbers
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
    if "urhongxin" in txt or "优尔鸿信" in txt: return "URHONGXIN"
    if "intertek" in txt: return "INTERTEK"
    if "cti" in txt or "centre testing" in txt or "华测检测" in txt: return "CTI"
    if "tuv" in txt: return "TUV"
    return "OTHERS"

# --- 3. Intertek 專用解析模組 (v71.0) ---

def scan_row_for_value(row_cells):
    """
    Intertek 行內直讀邏輯 - v71.0 改良版
    1. 修正數值提取：支援 '1381 (#2)' 格式。
    2. 優先權：數字(非黑名單) > Negative > N.D.
    """
    candidates_num = []
    has_negative = False
    has_nd = False

    for cell in row_cells:
        txt = clean_text(cell)
        if not txt: continue
        txt_lower = txt.lower()

        # 1. 排除明顯雜訊
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

        # 4. 偵測數字 (v71.0: 增強提取邏輯)
        # 使用 Regex 提取開頭的數字部分，忽略後面的 (#2)
        match_num = re.search(r"^(\d+(\.\d+)?)", txt)
        if match_num:
            try:
                # 提取純數字用於黑名單檢查
                val_str = match_num.group(1)
                val = float(val_str)
                
                # 黑名單過濾 (0.5, 1000 等)
                if val not in VALUE_BLACKLIST:
                    # 儲存 (原始字串 cleaned, 數值大小)
                    # 保留 txt 或 val_str? 
                    # 為了避免帶入 (#2) 導致 Excel 格式問題，我們回傳 val_str (純數字字串)
                    # 除非使用者堅持要 (#2)，但通常數據分析只需要數字
                    candidates_num.append((val_str, val))
            except:
                pass

    # 決策邏輯
    if candidates_num:
        # 取數值最大的 (避免抓到同行的 0.5 這種小 MDL)
        best_match = sorted(candidates_num, key=lambda x: x[1], reverse=True)[0]
        return best_match[0] # 回傳字串 "1381"
    
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
            # 表格過濾：黑名單
            header_rows = table[:3]
            header_str = " ".join([str(c) for row in header_rows for c in row if c]).lower()
            
            if "restricted substances" in header_str and "limits" in header_str: continue
            if "sample description" in header_str or "product name" in header_str or "item no" in header_str: continue
            if "cas no" in header_str and "name" in header_str: continue

            for row in table:
                clean_row = [clean_text(cell) for cell in row if cell]
                if not clean_row: continue
                row_text = " ".join(clean_row).lower()

                for target, keywords in TARGET_MAP:
                    hit = False
                    for kw in keywords:
                        if kw in row_text:
                            if target in ["Pb", "Cd", "Hg"] and ("poly" in row_text or "pbb" in row_text):
                                continue
                            hit = True
                            break
                    
                    if hit:
                        val = scan_row_for_value(clean_row)
                        if val:
                            # 優先權：Negative(4) > 數字(3) > ND(1)
                            # 因為 scan_row_for_value 已經內部處理了 數字 > ND，這裡只需區分 Negative
                            if "negative" in val.lower(): priority_score = 4
                            elif "nd" in val.lower(): priority_score = 1
                            else: priority_score = 5 # 數字
                            
                            try:
                                real_val_num = float(re.sub(r"[<>]", "", val))
                            except:
                                real_val_num = 0

                            data_pool[target].append({
                                "priority": (priority_score, real_val_num, val),
                                "filename": filename
                            })

# --- 4. SGS/CTI/Generic 解析模組 ---

def parse_sgs_cti_generic(pdf, filename, company, data_pool, debug_logs):
    full_text = ""
    for p in pdf.pages: full_text += (p.extract_text() or "")
    if any(k.lower() in full_text.lower() for k in PFAS_KEYWORDS):
         if not data_pool["PFAS"]:
             data_pool["PFAS"].append({"priority": (5, 0, "REPORT"), "filename": filename})

    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
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
    st.set_page_config(page_title="SGS/CTI/Intertek Tool v71.0", layout="wide")
    st.title("📄 萬用型檢測報告聚合工具 (v71.0 Intertek 數值修復版)")
    st.info("💡 v71.0：修正 Pb '1381 (#2)' 解析問題；修正 Cd 誤抓 '0.5' MDL 問題；全數保留原始小數點格式。")

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
