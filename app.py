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

def extract_dates_universal(text):
    """
    v56.0: 萬能格式支援 (基於 v20.0 邏輯) + 積分過濾 (v55.0)
    策略：先將所有標點符號替換為空白，再用寬鬆正則抓取。
    """
    lines = text.split('\n')
    candidates = [] # (score, date_object)
    
    # 權重設定
    bonus_kw = ["report date", "issue date", "date:", "日期:", "dated"]
    poison_kw = [
        "approve", "approved", "approval", "approver", 
        "check", "checked", "review", "reviewed",      
        "receive", "received", "receipt",
        "period", "testing period", "started", "from", "to ",
        "承認", "核准", "檢驗", "收件", "有效", "expiry", "valid", "期間"
    ]

    # 萬能正則 (針對已清洗過，只剩空白分隔的字串)
    # 1. YYYY MM DD (2025 01 08)
    pat_ymd = r"(20\d{2})\s+(0?[1-9]|1[0-2])\s+(0?[1-9]|[12][0-9]|3[01])"
    # 2. DD Mon YYYY (06 Jan 2025)
    pat_dmy = r"(0?[1-9]|[12][0-9]|3[01])\s+([a-zA-Z]{3,})\s+(20\d{2})"
    # 3. Mon DD YYYY (Jan 08 2025)
    pat_mdy = r"([a-zA-Z]{3,})\s+(0?[1-9]|[12][0-9]|3[01])\s+(20\d{2})"
    # 4. 中文 (2025年01月08日) - 這個不用清洗，直接抓
    pat_zh = r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月\s*(0?[1-9]|[12][0-9]|3[01])\s*日"

    for line in lines:
        line_lower = line.lower()
        
        # 1. 計算分數
        score = 1
        if any(bad in line_lower for bad in poison_kw):
            score = -100 # 毒藥
        elif any(good in line_lower for good in bonus_kw):
            score = 100 # 黃金

        # 2. 暴力清洗：把標點符號都變成空白，讓格式統一
        # 保留中文字，但把 .,-/ 都換成空白
        clean_line = line.replace(".", " ").replace(",", " ").replace("-", " ").replace("/", " ")
        # 壓縮連續空白
        clean_line = " ".join(clean_line.split())

        # 3. 嘗試匹配
        found_dt = None
        
        # 中文優先 (因為格式最固定)
        zh_match = re.search(pat_zh, line) # 用原行抓中文
        if zh_match:
            try:
                y, m, d = zh_match.groups()
                found_dt = datetime(int(y), int(m), int(d))
            except: pass
        
        if not found_dt:
            # 英文格式 (用清洗後的行)
            for pat in [pat_ymd, pat_dmy, pat_mdy]:
                matches = re.finditer(pat, clean_line, re.IGNORECASE)
                for match in matches:
                    try:
                        groups = match.groups()
                        date_str = " ".join(groups)
                        # 嘗試多種解析格式
                        for fmt in ["%Y %m %d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"]:
                            try:
                                dt = datetime.strptime(date_str, fmt)
                                if 2000 <= dt.year <= 2030:
                                    found_dt = dt
                                    break
                            except: continue
                        if found_dt: break
                    except: continue
                if found_dt: break
        
        if found_dt:
            candidates.append((score, found_dt))
            
    return candidates

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
        for key, keywords in SIMPLE_KEYWORDS.items():
            if targets and key not in targets: continue
            for kw in keywords:
                if kw.lower() in line_clean.lower() and "test item" not in line_clean.lower():
                    matched_simple = key
                    break
            if matched_simple: break
        
        matched_group = None
        if not matched_simple:
            for group_key, keywords in GROUP_KEYWORDS.items():
                if targets and group_key not in targets: continue
                for kw in keywords:
                    if kw.lower() in line_clean.lower():
                        matched_group = group_key
                        break
                if matched_group: break
        
        if matched_simple or matched_group:
            parts = line_clean.split()
            if len(parts) < 2: continue
            
            found_val = ""
            for part in reversed(parts):
                p_lower = part.lower()
                if p_lower in ["mg/kg", "ppm", "2", "5", "10", "50", "100", "1000", "0.1", "-", "---", "unit", "mdl"]: continue
                if "nd" in p_lower:
                    found_val = "N.D."
                    break
                if re.match(r"^\d+.*$", part): 
                    val_check = part.replace("▲", "").replace("△", "")
                    try:
                        f = float(val_check)
                        if f not in [100.0, 1000.0, 50.0, 25.0, 20.0, 10.0, 5.0, 2.0]:
                            found_val = part
                            break
                    except: pass
            
            if found_val:
                priority = parse_value_priority(found_val)
                if priority[0] == 0: continue
                if matched_simple:
                    data_pool[matched_simple].append({"priority": priority, "filename": filename})
                elif matched_group:
                    file_group_data[matched_group].append(priority)

# --- 主程式 ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_file_valid_dates = [] 
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        
        try:
            with pdfplumber.open(file) as pdf:
                start_page_idx = find_report_start_page(pdf)
                
                # 判斷公司
                company = "OTHERS"
                if len(pdf.pages) > start_page_idx:
                    first_relevant_page_text = pdf.pages[start_page_idx].extract_text() or ""
                    company = identify_company(first_relevant_page_text)
                    if check_pfas_in_summary(first_relevant_page_text):
                        data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

                # v56.0: 日期提取 - 使用萬能格式 + 積分系統
                file_dates_candidates = []
                full_text_content = "" 
                
                for p_idx in range(start_page_idx, len(pdf.pages)):
                    page = pdf.pages[p_idx]
                    page_txt = page.extract_text() or ""
                    full_text_content += page_txt + "\n"
                    
                    if p_idx < start_page_idx + 5:
                        dates = extract_dates_universal(page_txt)
                        file_dates_candidates.extend(dates)
                
                if file_dates_candidates:
                    # 排序: 分數高優先 -> 日期晚優先
                    best_date = sorted(file_dates_candidates, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    # 只有正分的日期才算有效日期 (排除 Testing Period)
                    if best_date[0] > -50:
                        all_file_valid_dates.append(best_date[1])

                # 2. 引擎 A
                for p_idx in range(start_page_idx, len(pdf.pages)):
                    page = pdf.pages[p_idx]
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx, is_skip_table = identify_columns_by_company(table, company)
                        if is_skip_table: continue 

                        for row_idx, row in enumerate(table):
                            clean_row = [clean_text(cell) for cell in row]
                            row_txt = "".join(clean_row).lower()
                            if "test item" in row_txt or "result" in row_txt or "restricted" in row_txt: continue
                            if not any(clean_row): continue
                            
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            if "pvc" in item_name.lower() or "polyvinyl" in item_name.lower(): continue

                            result = ""
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            if not result:
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    if "nd" in c_lower or "n.d." in c_lower or "negative" in c_lower:
                                        result = cell
                                        break
                                    if re.search(r"^\d+(\.\d+)?", cell):
                                        try:
                                            if float(cell) in [1000, 100, 50, 25, 20, 10, 5, 2]: continue
                                        except: pass
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower(): continue 
                                        data_pool[target_key].append({"priority": priority, "filename": filename})
                                        break

                            for group_key, keywords in GROUP_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        file_group_data[group_key].append(priority)
                                        break
                
                # 3. 引擎 B
                missing_targets = []
                pb_data = [d for d in data_pool["Pb"] if d['filename'] == filename]
                if not pb_data: missing_targets.append("Pb")
                if not file_group_data["PBB"]: missing_targets.append("PBB")
                if not file_group_data["PBDE"]: missing_targets.append("PBDE")
                
                if missing_targets:
                    parse_text_lines(full_text_content, data_pool, file_group_data, filename, company, targets=missing_targets)

            # 檔案結算
            for group_key, values in file_group_data.items():
                if values:
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    data_pool[group_key].append({
                        "priority": best_in_file,
                        "filename": filename
                    })

        except Exception as e:
            st.warning(f"檔案 {filename} 解析異常: {e}")
        
        progress_bar.progress((i + 1) / len(files))

    # 聚合
    final_row = {}
    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]

    # 日期：取所有檔案中選出的「最佳日期」裡最晚的一個
    final_date_str = ""
    if all_file_valid_dates:
        latest_date = max(all_file_valid_dates)
        final_date_str = latest_date.strftime("%Y/%m/%d")
    
    final_row["日期"] = final_date_str
    
    pb_candidates = data_pool.get("Pb", [])
    if pb_candidates:
        best_pb = sorted(pb_candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row["檔案名稱"] = best_pb['filename']
    else:
        final_row["檔案名稱"] = files[0].name if files else ""

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v56.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v56.0 萬能格式回歸版)")
st.info("💡 v56.0：回歸 v20.0 的萬能日期格式邏輯 (暴力清洗標點)，並結合 v55.0 的積分過濾，完美支援 CTI/Intertek 複雜格式，同時排除承認日期。")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 重新執行"): st.rerun()

    try:
        result_data = process_files(uploaded_files)
        df = pd.DataFrame(result_data)
        for col in OUTPUT_COLUMNS:
            if col not in df.columns: df[col] = ""
        df = df[OUTPUT_COLUMNS]

        st.success("✅ 處理完成！")
        st.dataframe(df)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v56.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
