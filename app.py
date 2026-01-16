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
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI", "Cr6+"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "Bis(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "Perfluorooctane sulfonic acid", "全氟辛烷磺酸"],
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘"]
}

GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴聯苯總和",
        "Polybromobiphenyl", "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", "Decabromobiphenyl"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和",
        "Polybromodiphenyl ether", "Monobromodiphenyl ether", "Dibromodiphenyl ether", 
        "Tribromodiphenyl ether", "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", 
        "Hexabromodiphenyl ether", "Heptabromodiphenyl ether", "Octabromodiphenyl ether", 
        "Nonabromodiphenyl ether", "Decabromodiphenyl ether"
    ]
}

PFAS_SUMMARY_KEYWORDS = [
    "Per- and Polyfluoroalkyl Substances", "PFAS", "全氟/多氟烷基物質", "全氟烷基物質"
]

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "PFAS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

# --- 2. 輔助功能 (v35.1 修復版) ---

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def extract_date_from_text(text):
    text = clean_text(text)
    # v35.1: 修復日期抓取，新增對 "03 Mar 2023" (空白分隔) 的支援，並針對 "Date:" 標籤優化
    patterns = [
        # 優先權 1: 明確的 Date: dd Mon yyyy (SGS 常見格式)
        r"Date\s*[:\.]?\s*(0?[1-9]|[12][0-9]|3[01])\s+([a-zA-Z]{3})\s+(20\d{2})",
        # 優先權 2: dd-Mon-yyyy (Intertek 常見)
        r"(0?[1-9]|[12][0-9]|3[01])\s*[-/]\s*([a-zA-Z]{3})\s*[-/]\s*(20\d{2})",
        # 優先權 3: Mon dd, yyyy
        r"([a-zA-Z]{3})\.?\s+(0?[1-9]|[12][0-9]|3[01])[,\s]+\s*(20\d{2})",
        # 優先權 4: 標準 yyyy/mm/dd
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])"
    ]
    
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                full_match = match.group(0)
                # 移除 Date: 前綴，只留日期部分
                if "date" in full_match.lower():
                    full_match = re.sub(r"Date\s*[:\.]?\s*", "", full_match, flags=re.IGNORECASE)
                
                clean_str = re.sub(r"[,./-]", " ", full_match) # 統一分隔符
                clean_str = " ".join(clean_str.split())
                
                # 嘗試多種解析格式
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

def is_suspicious_limit_value(val):
    try:
        n = float(val)
        if n in [1000.0, 100.0, 50.0, 10.0, 5.0, 2.0]: return True
        return False
    except: return False

def parse_value_priority(value_str):
    raw_val = clean_text(value_str)
    if "(" in raw_val: raw_val = raw_val.split("(")[0].strip()
    
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    # v35.1: 強制過濾法規字串 (修復 2011/65/EU 問題)
    if "eu" in val_lower or "directive" in val_lower or "annex" in val_lower or "/" in val_lower:
        # 除非是 N.D./N.A. 否則有斜線通常是雜訊
        if "n.d" not in val_lower and "n/a" not in val_lower:
            return (0, 0, "")

    ignore_list = ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "no.1", "---", "-", "limits", "requirement", "conclusion", "pass", "fail"]
    if val_lower in ignore_list: return (0, 0, "")
    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    
    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower or "not detected" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "NEGATIVE")
    
    num_only_match = re.search(r"^[\d\.]+$", val)
    if num_only_match:
        if is_suspicious_limit_value(val): return (0, 0, "")

    num_match = re.search(r"^([\d\.]+)\s*(.*)$", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            full_str = val 
            return (3, number, full_str)
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
    if "tuv" in txt: return "TUV"
    return "OTHERS"

# --- 3. 核心：表格識別 ---

def identify_columns_by_company(table, company):
    item_idx = -1
    result_idx = -1
    
    max_scan_rows = min(4, len(table))
    
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if "test item" in txt or "tested item" in txt or "測試項目" in txt or "substance name" in txt:
                if item_idx == -1: item_idx = c_idx
                
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            if "limit" in txt or "mdl" in txt or "rl" in txt or "unit" in txt or "method" in txt or "cas" in txt: continue

            if company == "SGS":
                if "result" in txt or "結果" in txt or re.search(r"\b(no\.|00[1-9])", txt):
                     if result_idx == -1: result_idx = c_idx
            elif company == "INTERTEK":
                if "result" in txt or "claimed" in txt:
                     if result_idx == -1: result_idx = c_idx
            else:
                if "result" in txt or "結果" in txt:
                     if result_idx == -1: result_idx = c_idx

    if item_idx == -1: item_idx = 0
    if result_idx == -1 and len(table[0]) > 2: result_idx = len(table[0]) - 1

    return item_idx, result_idx

# --- 4. 核心：文字模式 ---

def parse_text_lines(text, data_pool, file_group_data, filename):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        # v35.1: 文字模式也要過濾法規標題行
        if "directive" in line_clean.lower() and "2011/65" in line_clean: continue

        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            for kw in keywords:
                if kw in line_clean and "test item" not in line_clean.lower():
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
                priority = parse_value_priority(part)
                if priority[0] > 0:
                    found_val = part
                    break
            
            if found_val:
                priority = parse_value_priority(found_val)
                if matched_simple:
                    data_pool[matched_simple].append({"priority": priority, "filename": filename})
                elif matched_group:
                    file_group_data[matched_group].append(priority)

# --- 主程式 ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    
    global_tracker = {
        "Pb": {"max_score": -1, "max_value": -1.0, "filename": ""}
    }
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        
        try:
            with pdfplumber.open(file) as pdf:
                file_dates = []
                full_text_content = "" 
                
                # 1. 提取日期與公司
                for p_idx, page in enumerate(pdf.pages):
                    page_txt = page.extract_text() or ""
                    full_text_content += page_txt + "\n"
                    if p_idx < 3: 
                        d = extract_date_from_text(page_txt)
                        if d: file_dates.append(d)
                
                if file_dates: all_dates.append((max(file_dates), filename))
                company = identify_company(full_text_content[:2000])
                
                if check_pfas_in_summary(full_text_content[:2000]):
                    data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

                # 2. 引擎 A: 表格模式
                has_table_data = False
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx = identify_columns_by_company(table, company)
                        if result_idx == -1: continue

                        for row in table:
                            clean_row = [clean_text(cell) for cell in row]
                            if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
                            
                            item_name = clean_row[item_idx]
                            
                            # v35.1: 關鍵過濾 - 如果這一行看起來像法規標題，直接跳過
                            item_name_lower = item_name.lower()
                            if "directive" in item_name_lower or "annex" in item_name_lower or "2011/65" in item_name_lower:
                                continue
                            if "test item" in item_name_lower or "result" in item_name_lower: continue
                            
                            result_cell = ""
                            if result_idx < len(clean_row):
                                result_cell = clean_row[result_idx]
                            
                            if not result_cell:
                                for cell in clean_row:
                                    if "n.d." in cell.lower() or "not detected" in cell.lower():
                                        result_cell = cell
                                        break

                            priority = parse_value_priority(result_cell)
                            if priority[0] == 0: continue
                            
                            has_table_data = True

                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and ("related" in item_name.lower() or "derivative" in item_name.lower()): continue
                                        
                                        data_pool[target_key].append({"priority": priority, "filename": filename})
                                        
                                        if target_key == "Pb":
                                            score, val, _ = priority
                                            if score > global_tracker["Pb"]["max_score"]:
                                                global_tracker["Pb"]["max_score"] = score
                                                global_tracker["Pb"]["max_value"] = val
                                                global_tracker["Pb"]["filename"] = filename
                                            elif score == global_tracker["Pb"]["max_score"] and val > global_tracker["Pb"]["max_value"]:
                                                global_tracker["Pb"]["max_value"] = val
                                                global_tracker["Pb"]["filename"] = filename
                                        break
                            
                            for group_key, keywords in GROUP_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        file_group_data[group_key].append(priority)
                                        break
                
                # 3. 引擎 B: 文字模式
                pb_found_in_file = any(d['filename'] == filename for d in data_pool["Pb"])
                if not pb_found_in_file or (company == "SGS" and not has_table_data):
                    parse_text_lines(full_text_content, data_pool, file_group_data, filename)
                    
                    for d in data_pool["Pb"]:
                         if d['filename'] == filename:
                             p = d['priority']
                             if p[0] > global_tracker["Pb"]["max_score"]:
                                 global_tracker["Pb"]["max_score"] = p[0]
                                 global_tracker["Pb"]["max_value"] = p[1]
                                 global_tracker["Pb"]["filename"] = filename
                             elif p[0] == global_tracker["Pb"]["max_score"] and p[1] > global_tracker["Pb"]["max_value"]:
                                 global_tracker["Pb"]["max_value"] = p[1]
                                 global_tracker["Pb"]["filename"] = filename

            for group_key, values in file_group_data.items():
                if values:
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    data_pool[group_key].append({
                        "priority": best_in_file,
                        "filename": filename
                    })

        except Exception as e:
            st.warning(f"⚠️ 檔案 {filename} 解析異常: {e}")
        
        progress_bar.progress((i + 1) / len(files))

    # --- 最終聚合 ---
    final_row = {}
    
    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]

    final_date_str = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
    
    final_row["日期"] = final_date_str
    
    if global_tracker["Pb"]["filename"]:
        final_row["檔案名稱"] = global_tracker["Pb"]["filename"]
    else:
        final_row["檔案名稱"] = latest_date_record[1] if all_dates else (files[0].name if files else "Unknown")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS/Intertek 報告聚合工具 v35.1", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v35.1 修復版)")
st.error("🛠️ v35.1 修復：解決 SGS 日期為空、Pb 誤抓法規編號 (2011/65/EU) 的問題。")

uploaded_files = st.file_uploader("請選取 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 開始分析"):
        try:
            result_data = process_files(uploaded_files)
            df = pd.DataFrame(result_data)
            for col in OUTPUT_COLUMNS:
                if col not in df.columns: df[col] = ""
            df = df[OUTPUT_COLUMNS]

            st.success("✅ 分析完成！")
            st.dataframe(df)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Summary')
            
            st.download_button("📥 下載 Excel", data=output.getvalue(), file_name=f"Summary_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                
        except Exception as e:
            st.error(f"錯誤: {e}")
