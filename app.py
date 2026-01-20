import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 關鍵字與黑名單定義 ---

# Intertek 專用數值黑名單 (SGS/CTI 不使用此名單，以免誤殺)
VALUE_BLACKLIST_INTERTEK = [
    1000.0, 100.0, 50.0, 25.0, 20.0, 10.0, 8.0, 5.0, 2.0, 1.0, 
    0.5, 0.1, 0.05, 0.01, # MDLs
    2011.0, 2015.0, 2016.0, 2017.0, 2023.0, 2024.0, 2025.0, # Years
    62321.0, 3052.0, 14582.0, 3540.0, 17681.0, 18219.0, 15968.0, 111.0
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

# SGS/CTI 用的標準關鍵字映射
SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "铅", "Pb", "납"], 
    "Cd": ["Cadmium", "镉", "Cd", "카드뮴"], 
    "Hg": ["Mercury", "汞", "Hg", "수은"], 
    "Cr6+": ["Hexavalent Chromium", "六价铬", "六價鉻", "Cr(VI)", "Chromium VI", "Cr6+", "6가 크롬"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "邻苯二甲酸二(2-乙基己基)酯"],
    "BBP": ["BBP", "Butyl benzyl phthalate", "邻苯二甲酸丁苄酯"],
    "DBP": ["DBP", "Dibutyl phthalate", "邻苯二甲酸二丁酯"],
    "DIBP": ["DIBP", "Diisobutyl phthalate", "邻苯二甲酸二异丁酯"],
    "PFOS": ["Perfluorooctane sulfonates", "PFOS", "全氟辛烷磺酸"],
    "F": ["Fluorine", "氟", "(F)"],
    "CL": ["Chlorine", "氯", "(Cl)"],
    "BR": ["Bromine", "溴", "(Br)"],
    "I": ["Iodine", "碘", "(I)"]
}

GROUP_KEYWORDS = {
    "PBB": ["Polybrominated Biphenyls", "PBBs", "多溴联苯"],
    "PBDE": ["Polybrominated Diphenyl Ethers", "PBDEs", "多溴二苯醚"]
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

# --- 3. Intertek 專用解析模組 (保持 v72.0 狀態) ---

def scan_row_for_intertek(row_cells):
    """
    Intertek 行內直讀邏輯
    優先權：數字(非黑名單) > Negative > N.D.
    """
    candidates_num = []
    has_negative = False
    has_nd = False

    for cell in row_cells:
        txt = clean_text(cell)
        if not txt: continue
        txt_lower = txt.lower()

        if any(x in txt_lower for x in ["mg/kg", "ppm", "µg", "%", "iec", "epa", "iso", "method", "reference", "limit", "mdl", "loq"]):
            continue

        if "negative" in txt_lower:
            has_negative = True
            continue

        if "nd" in txt_lower or "n.d." in txt_lower or "not detected" in txt_lower:
            has_nd = True
            continue

        match_num = re.search(r"^(\d+(\.\d+)?)", txt)
        if match_num:
            try:
                val_str = match_num.group(1)
                val = float(val_str)
                if val not in VALUE_BLACKLIST_INTERTEK:
                    candidates_num.append((val_str, val))
            except: pass

    if candidates_num:
        best_match = sorted(candidates_num, key=lambda x: x[1], reverse=True)[0]
        return best_match[0]
    
    if has_negative: return "Negative"
    if has_nd: return "N.D."

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
    for page in pdf.pages:
        text = page.extract_text() or ""
        full_text_content += text
    
    if any(kw.lower() in full_text_content.lower() for kw in PFAS_KEYWORDS):
        if not data_pool["PFAS"]:
            data_pool["PFAS"].append({"priority": (5, 0, "REPORT"), "filename": filename})

    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
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
                    hit_cell_index = -1
                    
                    for idx, cell in enumerate(clean_row):
                        cell_lower = cell.lower()
                        for kw in keywords:
                            if kw in cell_lower:
                                if target in ["Pb", "Cd", "Hg"] and ("poly" in cell_lower or "pbb" in cell_lower):
                                    continue
                                hit = True
                                hit_cell_index = idx
                                break
                        if hit: break
                    
                    if hit:
                        cells_to_scan = clean_row[hit_cell_index+1:]
                        val = scan_row_for_intertek(cells_to_scan)
                        if val:
                            if "negative" in val.lower(): priority_score = 4
                            elif "nd" in val.lower(): priority_score = 1
                            else: priority_score = 5
                            
                            try:
                                real_val_num = float(re.sub(r"[<>]", "", val))
                            except:
                                real_val_num = 0

                            data_pool[target].append({
                                "priority": (priority_score, real_val_num, val),
                                "filename": filename
                            })

# --- 4. SGS 專用解析模組 (復刻 v53 版) ---

def parse_table_sgs_legacy(table, filename, data_pool, debug_logs, sample_id=None):
    item_idx = -1; result_idx = -1; mdl_idx = -1; limit_idx = -1; unit_idx = -1
    
    max_scan_rows = min(5, len(table))
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            if "test item" in txt or "tested item" in txt or "測試項目" in txt or "检测项目" in txt: item_idx = c_idx
            if "mdl" in txt or "loq" in txt: mdl_idx = c_idx
            if "limit" in txt or "限值" in txt: limit_idx = c_idx
            if "unit" in txt or "单位" in txt: unit_idx = c_idx
            
            # Result 偵測
            if sample_id and sample_id.lower() in txt:
                result_idx = c_idx
            elif "result" in txt or "結果" in txt or "检测結果" in txt or "No." in txt:
                if result_idx == -1: result_idx = c_idx

    if item_idx == -1: item_idx = 0
    
    # Fallback to last column for Result if not found
    if result_idx == -1:
        cols = len(table[0])
        if cols > 1 and (cols - 1) not in [item_idx, mdl_idx, limit_idx, unit_idx]:
             result_idx = cols - 1

    for row in table:
        clean_row = [clean_text(cell) for cell in row]
        if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
        
        item_name = clean_row[item_idx]
        item_name_clean = item_name.lower().replace("\n", "")

        # 匹配邏輯
        for target, kws in SIMPLE_KEYWORDS.items():
            if any(k.lower() in item_name_clean for k in kws):
                res_val = ""
                if result_idx != -1 and result_idx < len(clean_row):
                    res_val = clean_row[result_idx]
                
                # 如果結果欄位沒抓到，嘗試在同一行找 ND 或數字
                if not res_val:
                    for i, cell in enumerate(clean_row):
                        if i in [item_idx, mdl_idx, limit_idx, unit_idx]: continue
                        if "nd" in cell.lower() or re.match(r"^\d+(\.\d+)?$", cell):
                            res_val = cell
                            break
                
                if res_val:
                    # 簡單判斷
                    prio = 1 if "nd" in res_val.lower() else 3
                    try:
                        num = float(re.sub(r"[<>]", "", res_val))
                    except: num = 0
                    
                    data_pool[target].append({"priority": (prio, num, res_val), "filename": filename})

        # Group logic (PBB/PBDE) for SGS
        for group, kws in GROUP_KEYWORDS.items():
            if any(k.lower() in item_name_clean for k in kws) and "sum" in item_name_clean:
                 res_val = clean_row[result_idx] if result_idx < len(clean_row) else "N.D."
                 if "nd" in res_val.lower():
                     data_pool[group].append({"priority": (1, 0, "N.D."), "filename": filename})

# --- 5. CTI 專用解析模組 (復刻 v49 版) ---

def parse_table_cti_legacy(table, filename, data_pool, debug_logs):
    item_idx = -1; result_idx = -1; mdl_idx = -1
    
    max_scan_rows = min(5, len(table))
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            if "test item" in txt or "测试项目" in txt or "substance name" in txt: item_idx = c_idx
            if "mdl" in txt or "loq" in txt or "检出限" in txt: mdl_idx = c_idx
            if "result" in txt or "结果" in txt or "00" in txt: 
                if result_idx == -1: result_idx = c_idx

    if item_idx == -1: item_idx = 0
    if result_idx == -1: return # CTI 必須找到結果欄

    for row in table:
        clean_row = [clean_text(cell) for cell in row]
        if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
        
        item_name = clean_row[item_idx]
        item_lower = item_name.lower().replace("\n", "")
        
        # 排除標題行
        if "test item" in item_lower or "result" in item_lower: continue

        res_val = ""
        if result_idx < len(clean_row):
            res_val = clean_row[result_idx]
        
        if not res_val: continue

        for target, kws in SIMPLE_KEYWORDS.items():
            if any(k.lower() in item_lower for k in kws):
                prio = 1 if "nd" in res_val.lower() else 3
                try: num = float(re.sub(r"[<>]", "", res_val))
                except: num = 0
                data_pool[target].append({"priority": (prio, num, res_val), "filename": filename})

        # CTI PBB/PBDE Group Header Logic (CTI usually puts Sum in a separate row or we infer N.D. if all subs are N.D.)
        # 這裡簡化處理：如果找到 PBBs 標題行且結果是 N.D.
        for group, kws in GROUP_KEYWORDS.items():
            if any(k.lower() in item_lower for k in kws) and "mono" not in item_lower:
                if "nd" in res_val.lower():
                    data_pool[group].append({"priority": (1, 0, "N.D."), "filename": filename})

# --- 6. Main Processing ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS}
    all_dates = []
    debug_logs = []
    
    for file in files:
        filename = file.name
        try:
            with pdfplumber.open(file) as pdf:
                # 0. 判斷廠商
                first_page_text = pdf.pages[0].extract_text() or ""
                company = identify_company(first_page_text)
                
                # 1. 抓日期
                for i in range(min(3, len(pdf.pages))):
                    d = extract_date_from_text(pdf.pages[i].extract_text())
                    if d: 
                        all_dates.append((d, filename))
                        break
                
                # 2. 全文掃描 PFAS (所有廠商通用)
                full_text_content = ""
                for page in pdf.pages: full_text_content += (page.extract_text() or "")
                if any(kw.lower() in full_text_content.lower() for kw in PFAS_KEYWORDS):
                    if not data_pool["PFAS"]:
                        data_pool["PFAS"].append({"priority": (5, 0, "REPORT"), "filename": filename})

                # 3. 分流
                if company == "INTERTEK":
                    process_intertek(pdf, filename, data_pool, debug_logs)
                elif company == "SGS":
                    # SGS 需提取 Sample ID
                    sample_id_match = re.search(r"Sample No\.\s*[:\.]?\s*([A-Za-z0-9]+)", first_page_text, re.IGNORECASE)
                    sid = sample_id_match.group(1) if sample_id_match else None
                    for page in pdf.pages:
                        tables = page.extract_tables()
                        for table in tables:
                            parse_table_sgs_legacy(table, filename, data_pool, debug_logs, sample_id=sid)
                elif company == "CTI":
                    for page in pdf.pages:
                        tables = page.extract_tables()
                        for table in tables:
                            parse_table_cti_legacy(table, filename, data_pool, debug_logs)
                else:
                    # 其他廠商使用 SGS 邏輯當作通用邏輯
                    for page in pdf.pages:
                        tables = page.extract_tables()
                        for table in tables:
                            parse_table_sgs_legacy(table, filename, data_pool, debug_logs)

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
    st.set_page_config(page_title="SGS/CTI/Intertek Tool v73.0", layout="wide")
    st.title("📄 萬用型檢測報告聚合工具 (v73.0 完美復原版)")
    st.info("💡 v73.0：1. Intertek 邏輯鎖定 (v72 狀態，解決 Cr6+/PBB/Limit)。 2. SGS/CTI 邏輯完全還原至舊版穩定狀態 (解決回歸空值問題)。 3. PFAS 報告檢測全廠商通用。")

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
