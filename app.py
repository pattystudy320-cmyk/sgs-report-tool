import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 定義欄位與關鍵字 ---

SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "鉛", "Pb"],
    "Cd": ["Cadmium", "鎘", "Cd"],
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "Bis(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": [
        "Perfluorooctane sulfonates", "Perfluorooctane sulfonate", 
        "Perfluorooctane sulfonic acid", "全氟辛烷磺酸"
    ], 
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘"]
}

GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴聯苯總和",
        "Polybromobiphenyl", "Polybromobiphenyls",
        "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", 
        "Monobrominated", "Dibrominated", "Tribrominated", 
        "Tetrabrominated", "Pentabrominated", "Hexabrominated", 
        "Heptabrominated", "Octabrominated", "Nonabrominated", 
        "Decabrominated", "bromobiphenyl"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和",
        "Polybromodiphenyl ether", "Polybromodiphenyl ethers",
        "Monobromodiphenyl ether", "Dibromodiphenyl ether", "Tribromodiphenyl ether",
        "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", "Hexabromodiphenyl ether",
        "Heptabromodiphenyl ether", "Octabromodiphenyl ether", "Nonabromodiphenyl ether",
        "Decabromodiphenyl ether", 
        "Monobrominated Diphenyl", "Dibrominated Diphenyl", "Tribrominated Diphenyl",
        "Tetrabrominated Diphenyl", "Pentabrominated Diphenyl", "Hexabrominated Diphenyl",
        "Heptabrominated Diphenyl", "Octabrominated Diphenyl", "Nonabrominated Diphenyl",
        "Decabrominated Diphenyl", "bromodiphenyl ether"
    ]
}

PFAS_SUMMARY_KEYWORDS = [
    "Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物質", "全氟烷基物質"
]

MSDS_HEADER_KEYWORDS = [
    "content", "composition", "concentration", "含量", "成分"
]

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

# --- 2. 輔助功能 ---

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def find_report_start_page(pdf):
    for i in range(min(10, len(pdf.pages))):
        text = (pdf.pages[i].extract_text() or "").lower()
        if "test report" in text or "測試報告" in text:
            return i
    return 0

def extract_dates_final(text):
    """
    v54.0: 終極過濾版
    嚴格排除 "Testing Period", "Received Date", "Approved Date"
    只保留純淨的報告日期。
    """
    lines = text.split('\n')
    valid_dates = [] 
    
    patterns = [
        # 2025/01/08, 2025-01-08
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])", 
        # 05-Feb-2024 (SGS)
        r"(0?[1-9]|[12][0-9]|3[01])\s*[-/]\s*([a-zA-Z]{3})\s*[-/]\s*(20\d{2})", 
        # Jan. 08, 2025 (CTI)
        r"([a-zA-Z]{3,})\.?\s+(0?[1-9]|[12][0-9]|3[01])[,\s]+\s*(20\d{2})",
        # 08 Jan, 2025 (Intertek)
        r"(0?[1-9]|[12][0-9]|3[01])\s+([a-zA-Z]{3,})\,?\s+(20\d{2})",
        # 中文
        r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月\s*(0?[1-9]|[12][0-9]|3[01])\s*日" 
    ]
    
    high_priority_kw = ["report date", "issue date", "date:", "日期:"]
    
    # ★ v54.0 關鍵修正：加入 "period", "started", "received" 到黑名單 ★
    block_kw = [
        "approve", "approved", "approval", "approver", 
        "check", "checked", "review", "reviewed",      
        "receive", "received", "receipt", # 排除收件日             
        "period", "duration", "started", "from", "to ", # 排除測試期間 (Testing Period: xxx to xxx)
        "承認", "核准", "檢驗", "收件", "有效", "expiry", "valid", "期間"
    ]

    for line in lines:
        line_lower = line.lower()
        
        # 1. 黑名單檢查 (只要有 Period/Received 就跳過)
        if any(bad in line_lower for bad in block_kw):
            continue 

        priority = 1
        if any(good in line_lower for good in high_priority_kw):
            priority = 2 

        for pattern in patterns:
            matches = re.finditer(pattern, line, re.IGNORECASE)
            for match in matches:
                try:
                    full_match = match.group(0)
                    clean_str = full_match.replace(",", " ").replace(".", " ").replace("-", " ").replace("/", " ").replace("年", " ").replace("月", " ").replace("日", " ")
                    clean_str = " ".join(clean_str.split())
                    
                    dt = None
                    formats = [
                        "%Y %m %d", 
                        "%d %b %Y", 
                        "%b %d %Y", 
                        "%B %d %Y", 
                        "%d %B %Y"
                    ]
                    
                    for fmt in formats:
                        try:
                            dt = datetime.strptime(clean_str, fmt)
                            if 2000 <= dt.year <= 2030:
                                valid_dates.append((priority, dt))
                                break
                        except: continue
                    if dt: break
                except: continue
    
    return valid_dates

def is_suspicious_limit_value(val):
    try:
        n = float(val)
        if n in [1000.0, 100.0, 50.0]: return True
        return False
    except: return False

def parse_value_priority(value_str):
    raw_val = clean_text(value_str)
    if "(" in raw_val: raw_val = raw_val.split("(")[0].strip()
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    if val_lower in ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "no.1", "---", "-", "limits"]: 
        return (0, 0, "")

    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    if is_suspicious_limit_value(val): return (0, 0, "") 

    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "NEGATIVE")
    
    num_match = re.search(r"([\d\.]+)", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, val)
        except: pass
            
    return (0, 0, val)

def check_pfas_in_summary(text):
    txt_lower = text.lower()
    for kw in PFAS_SUMMARY_KEYWORDS:
        if kw.lower() in txt_lower: return True
    return False

def identify_company(text):
    txt = text.lower()
    if "sgs" in txt: return "SGS"
    if "intertek" in txt: return "INTERTEK"
    if "cti" in txt or "centre testing" in txt: return "CTI"
    if "ctic" in txt: return "CTIC"
    return "OTHERS"

# --- 3. 核心：表格識別 ---

def identify_columns_by_company(table, company):
    item_idx = -1
    result_idx = -1
    mdl_idx = -1
    limit_idx = -1
    
    max_scan_rows = min(3, len(table))
    full_header_text = ""
    for r in range(max_scan_rows):
        full_header_text += " ".join([str(c).lower() for c in table[r] if c]) + " "

    is_msds_table = False
    if any(k in full_header_text for k in MSDS_HEADER_KEYWORDS) and "result" not in full_header_text:
        is_msds_table = True

    for r_idx in range(max_scan_rows):
        row = table[r_idx]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            if "test item" in txt or "tested item" in txt or "測試項目" in txt:
                if item_idx == -1: item_idx = c_idx
            if "mdl" in txt or "loq" in txt:
                if mdl_idx == -1: mdl_idx = c_idx
            if "limit" in txt or "限值" in txt:
                if limit_idx == -1: limit_idx = c_idx

            is_bad_header = any(bad in txt for bad in MSDS_HEADER_KEYWORDS)
            
            if not is_bad_header:
                if ("result" in txt or "結果" in txt or re.search(r"00[1-9]", txt) or 
                    re.search(r"^[a-z]?\s*-?\s*\d+$", txt) or "no." in txt):
                    if "cas" not in txt and "method" not in txt and "limit" not in txt:
                        if result_idx == -1: result_idx = c_idx

    if result_idx == -1 and (company == "SGS" or company == "CTIC"):
        if mdl_idx != -1 and mdl_idx + 1 < len(table[0]):
            result_idx = mdl_idx + 1
    
    is_reference_table = False
    if is_msds_table: 
        is_reference_table = True
    elif result_idx == -1:
        if "restricted substances" in full_header_text or "group name" in full_header_text or "substance name" in full_header_text:
            is_reference_table = True
        if company == "INTERTEK" and "limits" in full_header_text:
            is_reference_table = True
        if item_idx == -1:
            is_reference_table = True

    return item_idx, result_idx, is_reference_table

# --- 4. 核心：文字模式 ---

def parse_text_lines(text, data_pool, file_group_data, filename, company, targets=None):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        if any(bad in line_clean.lower() for bad in MSDS_HEADER_KEYWORDS): continue

        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items
