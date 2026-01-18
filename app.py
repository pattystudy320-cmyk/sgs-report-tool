import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 關鍵字定義 ---

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
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴联苯", "多溴聯苯", "폴리브롬화비페닐",
        "Polybrominated Biphenyls, PBBs", 
        "多溴联苯之和(PBB)", "多溴联苯之和",
        "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", "Tetrabromobiphenyl", 
        "Pentabromobiphenyl", "Hexabromobiphenyl", "Heptabromobiphenyl", "Octabromobiphenyl", 
        "Nonabromobiphenyl", "Decabromobiphenyl",
        "Monobrominated biphenyl", "Dibrominated biphenyl", "Tribrominated biphenyl", 
        "Tetrabrominated biphenyl", "Pentabrominated biphenyl", "Hexabrominated biphenyl", 
        "Heptabrominated biphenyl", "Octabrominated biphenyl", "Nonabrominated biphenyl", 
        "Decabrominated biphenyl",
        "MonoBB", "DiBB", "TriBB", "TetraBB", "PentaBB", "HexaBB", "HeptaBB", "OctaBB", "NonaBB", "DecaBB",
        "一溴联苯", "二溴联苯", "三溴联苯", "四溴联苯", "五溴联苯", "六溴联苯", "七溴联苯", "八溴联苯", "九溴联苯", "十溴联苯"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴二苯醚", "폴리브롬화디페닐에테르",
        "Polybrominated Diphenyl Ethers, PBDEs",
        "多溴二苯醚之和(PBDE)", "多溴二苯醚之和",
        "Monobromodiphenyl ether", "Dibromodiphenyl ether", "Tribromodiphenyl ether", 
        "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", "Hexabromodiphenyl ether", 
        "Heptabromodiphenyl ether", "Octabromodiphenyl ether", "Nonabromodiphenyl ether", 
        "Decabromodiphenyl ether",
        "Monobrominated diphenyl ether", "Dibrominated diphenyl ether", "Tribrominated diphenyl ether", 
        "Tetrabrominated diphenyl ether", "Pentabrominated diphenyl ether", "Hexabrominated diphenyl ether", 
        "Heptabrominated diphenyl ether", "Octabrominated diphenyl ether", "Nonabrominated diphenyl ether", 
        "Decabrominated diphenyl ether",
        "MonoBDE", "DiBDE", "TriBDE", "TetraBDE", "PentaBDE", "HexaBDE", "HeptaBDE", "OctaBDE", "NonaBDE", "DecaBDE",
        "一溴二苯醚", "二溴二苯醚", "三溴二苯醚", "四溴二苯醚", "五溴二苯醚", "六溴二苯醚", "七溴二苯醚", "八溴二苯醚", "九溴二苯醚", "十溴二苯醚"
    ]
}

PFAS_SUMMARY_KEYWORDS = [
    "Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物质", "全氟烷基物质"
]

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

BLACKLIST_NUMBERS = [
    6476, 3052, 14582, 62321, 17025, 2011, 2015, 2021, 2022, 2023, 2024, 2025
]

# --- 2. 通用輔助功能 ---

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
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])"
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
    if "intertek" in txt: return "INTERTEK"
    if "cti" in txt or "centre testing" in txt or "华测检测" in txt: return "CTI"
    if "tuv" in txt: return "TUV"
    return "OTHERS"

def check_pfas_in_summary(text):
    txt_lower = text.lower()
    for kw in PFAS_SUMMARY_KEYWORDS:
        if kw.lower() in txt_lower: return True
    return False

def is_suspicious_limit_value(val):
    try:
        n = float(val)
        if n in [1000.0, 100.0, 50.0]: return True
        return False
    except: return False

def parse_value_priority(value_str, target_key=None, is_table_result=False, is_text_mode=False):
    raw_val = clean_text(value_str)
    
    # 檢查是否帶有三角形符號 (v39.0 新增)
    has_flag = "▲" in raw_val or "△" in raw_val
    
    if re.match(r"^[\(\[]?\d+[\)\]]$", raw_val): return (0, 0, "") 
    if "(" in raw_val and not has_flag: raw_val = raw_val.split("(")[0].strip()
    
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    filter_keywords = ["iec", "iso", "epa", "gb/t", "directive", "annex", "mdl", "loq", "limit", "result", "unit", "method", "reference", "determination", "conclusion", "pass", "fail", "requirement", "---", "note", "remark"]
    if any(x in val_lower for x in filter_keywords): return (0, 0, "")
    
    if any(x in val for x in ["年", "月", "日", "开始", "执行", "standard"]): return (0, 0, "")
    if ":" in val: return (0, 0, "") 
    if "/" in val and "n/a" not in val_lower: return (0, 0, "")
    
    if val in ["026", "001", "002", "003", "004", "A16", "A1", "A3", "SN1"]: return (0, 0, "")

    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower or "not detected" in val_lower or "未检出" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "阴性" in val_lower: return (2, 0, "NEGATIVE")
    
    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    if re.search(r"\d{4,}-\d+", val): return (0, 0, "")

    # v39.0: 允許數字後面跟著符號
    num_match = re.search(r"^([\d\.]+)(.*)$", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            
            # v39.0: 如果有三角形標記，則豁免所有過濾規則 (這是絕對優先的超標值)
            if has_flag:
                return (4, number, val) # Priority 4, 最高級別
            
            if int(number) in BLACKLIST_NUMBERS: return (0, 0, "")
            if is_suspicious_limit_value(number): return (0, 0, "")

            is_halogen = target_key in ["F", "CL", "BR", "I"]
            if not is_halogen:
                if number > 3000: return (0, 0, "")
                if is_text_mode and number.is_integer() and number < 50:
                    return (0, 0, "")

            full_str = val 
            return (3, number, full_str)
        except: pass
            
    return (0, 0, val)

# --- 3. 獨立的表格解析器 ---

def parse_table_cti(table, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs):
    header_text = ""
    max_scan_rows = min(5, len(table))
    for r in range(max_scan_rows):
        header_text += " ".join([str(c).lower() for c in table[r] if c]) + " "
    
    if ("substance name" in header_text or "group name" in header_text or "cas no" in header_text) and \
       ("result" not in header_text and "结果" not in header_text):
        return

    item_idx = -1; result_idx = -1; mdl_idx = -1; limit_idx = -1; cas_idx = -1
    
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            if "test item" in txt or "测试项目" in txt or "substance name" in txt: item_idx = c_idx
            if "mdl" in txt or "loq" in txt or "检出限" in txt: mdl_idx = c_idx
            if "limit" in txt or "限值" in txt: limit_idx = c_idx
            if "cas" in txt: cas_idx = c_idx
            if "result" in txt or "结果" in txt: result_idx = c_idx

    if result_idx == -1: return 
    if item_idx == -1: item_idx = 0

    for row in table:
        clean_row = [clean_text(cell) for cell in row]
        if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
        
        item_name = clean_row[item_idx]
        item_name_lower = item_name.lower()
        if "test item" in item_name_lower or "result" in item_name_lower: continue
        if "method" in item_name_lower or "remark" in item_name_lower or "note" in item_name_lower: continue

        result_cell = ""
        if result_idx < len(clean_row):
            result_cell = clean_row[result_idx]
        
        if not result_cell:
            for col_idx, cell in enumerate(clean_row):
                if col_idx in [limit_idx, mdl_idx, cas_idx]: continue
                if "n.d." in cell.lower() or "not detected" in cell.lower() or "未检出" in cell.lower():
                    result_cell = cell
                    break
        
        process_row_data(item_name, result_cell, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs, is_table=True)

def parse_table_sgs(table, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs):
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
            
            if "result" in txt or "結果" in txt or "检测结果" in txt or re.search(r"\b(no\.|00[1-9])", txt) or re.search(r"[a-z]\d+", txt):
                if result_idx == -1 and "cas" not in txt and "limit" not in txt and "method" not in txt:
                    result_idx = c_idx

    if item_idx == -1: item_idx = 0
    
    if result_idx == -1:
        candidate_idx = len(table[0]) - 1
        while candidate_idx >= 0:
            if candidate_idx not in [item_idx, mdl_idx, limit_idx, unit_idx]:
                is_likely_result = False
                for r_chk in range(1, min(6, len(table))): 
                    if candidate_idx < len(table[r_chk]):
                        cell_val = clean_text(table[r_chk][candidate_idx]).lower()
                        if "nd" in cell_val or re.search(r"\d", cell_val):
                            is_likely_result = True
                            break
                if is_likely_result:
                    result_idx = candidate_idx
                    break
            candidate_idx -= 1

    for row in table:
        clean_row = [clean_text(cell) for cell in row]
        if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
        
        item_name = clean_row[item_idx]
        if "test item" in item_name.lower() or "result" in item_name.lower() or "limit" in item_name.lower() or "检测项目" in item_name: continue
        
        result_cell = ""
        if result_idx != -1 and result_idx < len(clean_row):
            result_cell = clean_row[result_idx]
        
        if not result_cell:
            for i, cell in enumerate(clean_row):
                if i in [limit_idx, mdl_idx, unit_idx]: continue
                if "n.d." in cell.lower() or "not detected" in cell.lower() or "未检出" in cell.lower():
                    result_cell = cell
                    break
                if re.match(r"^\d+(\.\d+)?$", clean_text(cell)):
                     if not is_suspicious_limit_value(cell):
                        result_cell = cell

        process_row_data(item_name, result_cell, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs, is_table=True)

def parse_table_generic(table, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs):
    item_idx = -1; result_idx = -1
    max_scan_rows = min(5, len(table))
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if "test item" in txt or "tested item" in txt: item_idx = c_idx
            if "result" in txt or "claimed" in txt: result_idx = c_idx

    if item_idx == -1: item_idx = 0
    if result_idx == -1 and len(table[0]) > 2: result_idx = len(table[0]) - 1

    for row in table:
        clean_row = [clean_text(cell) for cell in row]
        if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
        
        item_name = clean_row[item_idx]
        if "test item" in item_name.lower(): continue
        
        result_cell = ""
        if result_idx != -1 and result_idx < len(clean_row):
            result_cell = clean_row[result_idx]
        
        if not result_cell:
            for cell in clean_row:
                if "n.d." in cell.lower():
                    result_cell = cell
                    break

        process_row_data(item_name, result_cell, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs, is_table=True)

# --- 4. 核心處理邏輯 ---

def process_row_data(item_name, result_cell, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs, is_table):
    current_key = None
    item_lower = item_name.lower()
    
    for k, v in SIMPLE_KEYWORDS.items():
        if k == "BR":
            if any(x in item_lower for x in ["poly", "biphenyl", "ether", "hbcdd", "tbbp", "联苯", "二苯醚", "环十二烷", "双酚"]): continue
        if k == "CL":
            if any(x in item_lower for x in ["paraffin", "pvc", "chlorinated", "sccp", "mccp", "氯化", "聚氯"]): continue
        if k == "F":
             if any(x in item_lower for x in ["perfluoro", "pfo", "全氟"]): continue

        for kw in v:
            if kw in item_name or kw.lower() in item_lower:
                current_key = k
                break
        if current_key: break

    priority = parse_value_priority(result_cell, target_key=current_key, is_table_result=is_table, is_text_mode=False)
    if priority[0] == 0: return

    for target_key, keywords in SIMPLE_KEYWORDS.items():
        if target_key == "BR":
            if any(x in item_lower for x in ["poly", "biphenyl", "ether", "hbcdd", "tbbp", "联苯", "二苯醚", "环十二烷", "双酚"]): continue
        if target_key == "CL":
             if any(x in item_lower for x in ["paraffin", "pvc", "chlorinated", "sccp", "mccp", "氯化", "聚氯"]): continue
        if target_key == "F":
             if any(x in item_lower for x in ["perfluoro", "pfo", "全氟"]): continue

        for kw in keywords:
            if kw in item_name or kw.lower() in item_name.lower():
                if target_key == "PFOS" and ("related" in item_name.lower() or "derivative" in item_name.lower()): continue
                
                data_pool[target_key].append({"priority": priority, "filename": filename, "source": 2 if is_table else 1})
                found_elements_in_table.add(target_key)
                
                if debug_logs is not None:
                    debug_logs.append({
                        "File": filename, "Element": target_key, 
                        "Value": result_cell, "Type": "Table" if is_table else "Text"
                    })

                score, val, _ = priority
                if score > global_tracker[target_key]["max_score"]:
                    global_tracker[target_key]["max_score"] = score
                    global_tracker[target_key]["max_value"] = val
                    global_tracker[target_key]["filename"] = filename
                elif score == global_tracker[target_key]["max_score"] and val > global_tracker[target_key]["max_value"]:
                    global_tracker[target_key]["max_value"] = val
                    global_tracker[target_key]["filename"] = filename
                break
    
    for group_key, keywords in GROUP_KEYWORDS.items():
        for kw in keywords:
            if kw in item_name or kw.lower() in item_name.lower():
                file_group_data[group_key].append(priority)
                break

# --- 5. 文字模式解析 ---

def parse_text_lines(text, data_pool, file_group_data, filename, found_elements, debug_logs):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        line_lower = line_clean.lower()
        if "test method" in line_lower or "reference to" in line_lower: continue
        if "directive" in line_lower and "2011/65" in line_lower: continue
        if "remark" in line_lower or "note" in line_lower: continue 

        has_unit = any(u in line_lower for u in ["mg/kg", "ppm", "µg/cm", "%"])
        is_text_mode_strict = not has_unit 

        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            if key in found_elements: continue 
            
            if key == "BR":
                if any(x in line_lower for x in ["poly", "biphenyl", "ether", "hbcdd", "tbbp", "联苯", "二苯醚", "环十二烷", "双酚"]): continue
            if key == "F" and ("pfo" in line_lower or "全氟" in line_lower): continue
            
            for kw in keywords:
                if kw in line_clean and "test item" not in line_lower:
                    matched_simple = key
                    break
            if matched_simple: break
        
        matched_group = None
        if not matched_simple:
            for group_key, keywords in GROUP_KEYWORDS.items():
                for kw in keywords:
                    if kw in line_clean:
                        matched_group = group_key
                        break
                if matched_group: break
        
        if matched_simple or matched_group:
            parts = line_clean.split()
            if len(parts) < 2: continue
            
            found_val = ""
            for part in reversed(parts):
                p_lower = part.lower()
                if p_lower in ["mg/kg", "ppm", "uqt", "loq", "mdl", "---", "-"]: continue
                
                priority = parse_value_priority(part, target_key=matched_simple, is_table_result=False, is_text_mode=is_text_mode_strict)
                if priority[0] > 0:
                    found_val = part
                    break
            
            if found_val:
                priority = parse_value_priority(found_val, target_key=matched_simple, is_table_result=False, is_text_mode=is_text_mode_strict)
                if matched_simple:
                    data_pool[matched_simple].append({"priority": priority, "filename": filename, "source": 1})
                    debug_logs.append({
                        "File": filename, "Element": matched_simple, 
                        "Value": found_val, "Type": "Text", "Raw": line_clean
                    })
                elif matched_group:
                    file_group_data[matched_group].append(priority)

# --- 主程式 ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    debug_logs = []
    
    global_tracker = {key: {"max_score": -1, "max_value": -1.0, "filename": ""} for key in SIMPLE_KEYWORDS.keys()}
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        found_elements_in_table = set()
        
        try:
            with pdfplumber.open(file) as pdf:
                file_dates = []
                full_text_content = "" 
                
                for p_idx, page in enumerate(pdf.pages):
                    page_txt = page.extract_text() or ""
                    full_text_content += page_txt + "\n"
                    if p_idx < 3: 
                        d = extract_date_from_text(page_txt)
                        if d: file_dates.append(d)
                
                if file_dates: all_dates.append((max(file_dates), filename))
                company = identify_company(full_text_content[:2000])
                
                if check_pfas_in_summary(full_text_content[:2000]):
                    data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename, "source": 2})
                    debug_logs.append({"File": filename, "Element": "PFAS", "Value": "REPORT", "Type": "Summary"})

                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        if company == "CTI":
                            parse_table_cti(table, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs)
                        elif company == "SGS":
                            parse_table_sgs(table, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs)
                        else:
                            parse_table_generic(table, filename, data_pool, file_group_data, global_tracker, found_elements_in_table, debug_logs)
                
                parse_text_lines(full_text_content, data_pool, file_group_data, filename, found_elements_in_table, debug_logs)
                
                for k in SIMPLE_KEYWORDS.keys():
                    for d in data_pool[k]:
                         p = d['priority']
                         if p[0] > global_tracker[k]["max_score"]:
                             global_tracker[k]["max_score"] = p[0]
                             global_tracker[k]["max_value"] = p[1]
                             global_tracker[k]["filename"] = filename
                         elif p[0] == global_tracker[k]["max_score"] and p[1] > global_tracker[k]["max_value"]:
                             global_tracker[k]["max_value"] = p[1]
                             global_tracker[k]["filename"] = filename

            for group_key, values in file_group_data.items():
                if values:
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    data_pool[group_key].append({
                        "priority": best_in_file,
                        "filename": filename,
                        "source": 2 
                    })

        except Exception as e:
            st.warning(f"⚠️ 檔案 {filename} 解析異常: {e}")
        
        progress_bar.progress((i + 1) / len(files))

    final_row = {}
    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
        
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1], x.get('source', 0)), reverse=True)[0]
        final_row[key] = best_record['priority'][2]

    final_date_str = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
    
    final_file = ""
    if global_tracker["Pb"]["filename"]:
        final_file = global_tracker["Pb"]["filename"]
    elif global_tracker["Cd"]["filename"]:
        final_file = global_tracker["Cd"]["filename"]
    else:
        final_file = latest_date_record[1] if all_dates else (files[0].name if files else "Unknown")
        
    final_row["日期"] = final_date_str
    final_row["檔案名稱"] = final_file

    return [final_row], debug_logs

# --- 介面 ---
st.set_page_config(page_title="SGS/CTI 報告聚合工具 v39.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v39.0 最終豁免版)")
st.error("🛠️ v39.0：新增超標值豁免機制 (Exemption Rule)。即使是 '186802 ▲' 這種超大數值，只要帶有三角形標記，程式就會無視過濾規則，強制抓取並完整顯示，確保異常數據不被遺漏。")

uploaded_files = st.file_uploader("請選取 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 開始分析"):
        try:
            result_data, debug_logs = process_files(uploaded_files)
            df = pd.DataFrame(result_data)
            for col in OUTPUT_COLUMNS:
                if col not in df.columns: df[col] = ""
            df = df[OUTPUT_COLUMNS]

            st.success("✅ 分析完成！")
            st.dataframe(df)

            with st.expander("🕵️ 偵錯模式 (Debug Mode)"):
                if debug_logs:
                    st.dataframe(pd.DataFrame(debug_logs))
                else:
                    st.info("無抓取紀錄")

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Summary')
            
            st.download_button("📥 下載 Excel", data=output.getvalue(), file_name=f"Summary_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                
        except Exception as e:
            st.error(f"錯誤: {e}")
