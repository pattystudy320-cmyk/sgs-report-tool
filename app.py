import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 關鍵字與設定 ---

# Intertek 專用黑名單
VALUE_BLACKLIST_INTERTEK = [
    1000.0, 100.0, 50.0, 25.0, 20.0, 10.0, 8.0, 5.0, 2.0, 1.0, 
    0.5, 0.1, 0.05, 0.01, 
    2011.0, 2015.0, 2016.0, 2017.0, 2023.0, 2024.0, 2025.0,
    62321.0, 3052.0, 14582.0, 3540.0, 17681.0, 18219.0, 15968.0, 111.0
]

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

PFAS_KEYWORDS = ["Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物質"]

INTERTEK_SUB_KEYWORDS = [
    "monobrominated", "dibrominated", "tribrominated", "tetrabrominated", 
    "pentabrominated", "hexabrominated", "heptabrominated", "octabrominated", 
    "nonabrominated", "decabrominated", "monobb", "monobde"
]

# 通用關鍵字 (含中英文)
SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "铅", "Pb", "납"], 
    "Cd": ["Cadmium", "镉", "Cd", "카드뮴"], 
    "Hg": ["Mercury", "汞", "Hg", "수은"], 
    "Cr6+": ["Hexavalent Chromium", "六价铬", "六價鉻", "Cr(VI)", "Chromium VI", "Cr6+", "6가 크롬"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "邻苯二甲酸二(2-乙基己基)酯"],
    "BBP": ["BBP", "Butyl benzyl phthalate", "邻苯二甲酸丁苄酯"],
    "DBP": ["DBP", "Dibutyl phthalate", "邻苯二甲酸二丁酯"],
    "DIBP": ["DIBP", "Diisobutyl phthalate", "邻苯二甲酸二異丁酯"],
    "PFOS": ["Perfluorooctane sulfonates", "PFOS", "全氟辛烷磺酸"],
    "F": ["Fluorine", "氟", "(F)"],
    "CL": ["Chlorine", "氯", "(Cl)"],
    "BR": ["Bromine", "溴", "(Br)"],
    "I": ["Iodine", "碘", "(I)"]
}

GROUP_KEYWORDS = {
    "PBB": ["Polybrominated Biphenyls", "PBBs", "多溴聯苯", "多溴联苯", "Sum of PBBs", "多溴聯苯總和", "Polybromobiphenyl"],
    "PBDE": ["Polybrominated Diphenyl Ethers", "PBDEs", "多溴二苯醚", "多溴聯苯醚", "Sum of PBDEs", "多溴聯苯醚總和", "Polybromodiphenyl"]
}

# --- 2. 輔助函式 ---

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def clean_value_final(val):
    if not val: return ""
    # 移除特殊符號
    val = val.replace("▼", "").replace("▲", "").strip()
    # 移除單位
    val = re.sub(r"(mg/kg|ppm|%|µg/cm²)", "", val, flags=re.IGNORECASE).strip()
    
    val_lower = val.lower()
    # 處理 ND
    if "nd" in val_lower or "n.d." in val_lower or "not detected" in val_lower or "未檢出" in val_lower or "未检出" in val_lower:
        return "N.D."
    if "negative" in val_lower or "陰性" in val_lower:
        return "Negative"
    
    # 移除 < >
    val = re.sub(r"[<>]", "", val).strip()
    
    return val

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

# --- 3. INTERTEK 專用模組 (v72.0 邏輯) ---

def scan_row_for_intertek(row_cells):
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
                            val_cleaned = clean_value_final(val) 
                            
                            if "negative" in val_cleaned.lower(): priority_score = 4
                            elif "nd" in val_cleaned.lower(): priority_score = 1
                            else: priority_score = 5
                            
                            try:
                                real_val_num = float(re.sub(r"[<>]", "", val_cleaned))
                            except:
                                real_val_num = 0

                            data_pool[target].append({
                                "priority": (priority_score, real_val_num, val_cleaned),
                                "filename": filename
                            })

# --- 4. SGS / CTI 專用模組 (v54.2 終極復刻: Sample ID 絕對錨定 + 中文支援) ---

def parse_sgs_cti_v54_plus(pdf, filename, company, data_pool, debug_logs):
    """
    SGS/CTI v54.2 邏輯加強版：
    1. Sample ID 絕對優先：抓取 No.1, A1, 004 等編號。
    2. 移除嚴格的表頭特徵檢查：不再強制要有 Unit/MDL (因為中文報告可能寫中文)，改由 Sample ID 和 Result 關鍵字驅動。
    3. 數值清洗：移除 "Pass", "Fail" 等干擾。
    """
    
    # 1. 提取 Sample ID (擴充支援格式)
    extracted_ids = []
    full_text = ""
    for p in pdf.pages: full_text += (p.extract_text() or "") + "\n"
    
    # PFAS 檢查
    if any(k.lower() in full_text.lower() for k in PFAS_KEYWORDS):
         if not data_pool["PFAS"]:
             data_pool["PFAS"].append({"priority": (5, 0, "REPORT"), "filename": filename})

    if company == "SGS":
        # 支援 Sample No. A1, No.1, Sample ID 等
        matches = re.findall(r"Sample\s*(?:No\.?|ID)[\s:]*([A-Z0-9\.\-]+)", full_text, re.IGNORECASE)
        for m in matches: extracted_ids.append(m.strip())
        if "No.1" in full_text: extracted_ids.append("No.1")
        if "A1" in full_text: extracted_ids.append("A1")

    elif company == "CTI":
        matches = re.findall(r"Result\s*(00\d)", full_text, re.IGNORECASE)
        for m in matches: extracted_ids.append(m.strip())
        if "004" in full_text: extracted_ids.append("004")

    # 2. 表格處理
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 2: continue

            # --- A. 欄位定位 (核心：找 Sample ID 或 Result) ---
            item_idx = -1
            result_idx = -1
            
            header_rows = table[:5]
            
            for r_idx, row in enumerate(header_rows):
                for c_idx, cell in enumerate(row):
                    txt = clean_text(cell).lower()
                    
                    # 定位項目欄 (支援中文)
                    if "test item" in txt or "测试项目" in txt or "測試項目" in txt or "測 試 項 目" in txt:
                        item_idx = c_idx
                    
                    # 定位結果欄 (Sample ID > Result > 結果)
                    # 1. 優先匹配 Sample ID
                    for sid in extracted_ids:
                        if sid.lower() == txt or sid.lower() in txt:
                            result_idx = c_idx
                            break
                    if result_idx != -1: break

                    # 2. 其次匹配 Result / 結果 (但要避開 Requirement)
                    if ("result" in txt or "结果" in txt or "結果" in txt) and "requirement" not in txt and "limit" not in txt:
                        result_idx = c_idx

                if item_idx != -1 and result_idx != -1: break
            
            # Fallback
            if result_idx == -1 and len(table[0]) > 1: result_idx = len(table[0]) - 1
            if item_idx == -1: item_idx = 0

            # --- B. 內容抓取 ---
            for r_idx in range(len(table)):
                row = table[r_idx]
                if len(row) <= result_idx: continue
                
                item_name = clean_text(row[item_idx])
                # 跳過標題行
                if "test item" in item_name.lower() or "result" in item_name.lower() or "項目" in item_name: continue

                res_val = clean_text(row[result_idx])
                if not res_val: continue
                
                # 過濾 "Pass" / "Fail" (這通常是 Summary 表的特徵)
                if res_val.lower() in ["pass", "fail", "conclude", "符合"]: continue
                
                # 清洗數值
                res_val_cleaned = clean_value_final(res_val)

                # 匹配單項
                for target, kws in SIMPLE_KEYWORDS.items():
                    if any(k.lower() in item_name.lower() for k in kws):
                        prio = 1 if "nd" in res_val_cleaned.lower() else 3
                        try:
                            num = float(re.sub(r"[<>]", "", res_val_cleaned))
                        except: num = 0
                        
                        if num not in [2011, 2015, 62321]: 
                             data_pool[target].append({"priority": (prio, num, res_val_cleaned), "filename": filename})

                # 匹配群組 (PBB/PBDE) - 直球對決
                for group, kws in GROUP_KEYWORDS.items():
                    if any(k.lower() in item_name.lower() for k in kws):
                        # 只要有值就抓 (解決 SGS S1000-2M 問題)
                        if res_val_cleaned and "---" not in res_val_cleaned:
                             data_pool[group].append({"priority": (3, 0, res_val_cleaned), "filename": filename})

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
                    parse_sgs_cti_v54_plus(pdf, filename, company, data_pool, debug_logs)

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
    st.set_page_config(page_title="SGS/CTI/Intertek Tool v87.0", layout="wide")
    st.title("📄 萬用型檢測報告聚合工具 (v87.0 Sample ID 錨定版)")
    st.info("💡 v87.0：針對 SGS/CTI 回歸 v54.2 的 'Sample ID 錨定' 機制，移除過度嚴格的表格過濾，確保中文報告與特殊格式報告皆能正確讀取。Intertek 維持 v72 穩定版。")

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
