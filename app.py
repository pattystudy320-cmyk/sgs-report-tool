import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 定義欄位與關鍵字 ---

SIMPLE_KEYWORDS = {
    "Pb": ["Lead", "铅", "Pb"], 
    "Cd": ["Cadmium", "镉", "Cd"], 
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六价铬", "六價鉻", "Cr(VI)", "Chromium VI", "Cr6+"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate", "邻苯二甲酸二(2-乙基己基)酯"],
    "BBP": ["BBP", "Butyl benzyl phthalate", "邻苯二甲酸丁苄酯"],
    "DBP": ["DBP", "Dibutyl phthalate", "邻苯二甲酸二丁酯"],
    "DIBP": ["DIBP", "Diisobutyl phthalate", "邻苯二甲酸二异丁酯"],
    "PFOS": ["Perfluorooctane sulfonates", "PFOS", "全氟辛烷磺酸"],
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘"]
}

GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "多溴联苯", "多溴聯苯",
        "Monobromobiphenyl", "一溴联苯", "Dibromobiphenyl", "二溴联苯",
        "Tribromobiphenyl", "三溴联苯", "Tetrabromobiphenyl", "四溴联苯",
        "Pentabromobiphenyl", "五溴联苯", "Hexabromobiphenyl", "六溴联苯",
        "Heptabromobiphenyl", "七溴联苯", "Octabromobiphenyl", "八溴联苯",
        "Nonabromobiphenyl", "九溴联苯", "Decabromobiphenyl", "十溴联苯"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "多溴二苯醚",
        "Monobromodiphenyl ether", "一溴二苯醚", "Dibromodiphenyl ether", "二溴二苯醚",
        "Tribromodiphenyl ether", "三溴二苯醚", "Tetrabromodiphenyl ether", "四溴二苯醚",
        "Pentabromodiphenyl ether", "五溴二苯醚", "Hexabromodiphenyl ether", "六溴二苯醚",
        "Heptabromodiphenyl ether", "七溴二苯醚", "Octabromodiphenyl ether", "八溴二苯醚",
        "Nonabromodiphenyl ether", "九溴二苯醚", "Decabromodiphenyl ether", "十溴二苯醚"
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

# --- 2. 輔助功能 ---

def clean_text(text):
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def extract_date_from_text(text):
    text = clean_text(text)
    patterns = [
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

def is_suspicious_limit_value(val):
    try:
        n = float(val)
        if n in [1000.0, 100.0, 50.0, 10.0, 8.0, 5.0, 2.0]: return True
        return False
    except: return False

def parse_value_priority(value_str, target_key=None, is_text_mode=False):
    """
    v36.3: 增加 is_text_mode 參數，針對文字模式進行更嚴格的過濾
    """
    raw_val = clean_text(value_str)
    
    # 移除括號雜訊 (1) -> 丟棄
    if re.match(r"^[\(\[]?\d+[\)\]]$", raw_val): return (0, 0, "")
    
    if "(" in raw_val: raw_val = raw_val.split("(")[0].strip()
    
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    if any(x in val_lower for x in ["iec", "iso", "epa", "gb/t", "directive", "annex", "mdl", "loq", "limit", "result", "unit", "method", "reference", "determination", "conclusion", "pass", "fail", "requirement", "---", "note", "remark"]):
        return (0, 0, "")
    
    if any(x in val for x in ["年", "月", "日", "开始", "执行", "standard"]): 
        return (0, 0, "")

    if ":" in val: return (0, 0, "") 
    if "/" in val and "n/a" not in val_lower: return (0, 0, "")
    if val in ["026", "001", "002"]: return (0, 0, "")

    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower or "not detected" in val_lower or "未检出" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "阴性" in val_lower: return (2, 0, "NEGATIVE")
    
    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    if re.search(r"\d{4,}-\d+", val): return (0, 0, "")

    num_match = re.search(r"^([\d\.]+)\s*(.*)$", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            
            # 數值黑名單
            if int(number) in BLACKLIST_NUMBERS: return (0, 0, "")
            
            # v36.3: 文字模式雜訊過濾 (解決 PFOS=25, I=19)
            # 如果是純整數，且小於 100，且是文字模式，極大概率是頁碼或日期
            if is_text_mode and number.is_integer() and number < 100:
                return (0, 0, "")

            if is_suspicious_limit_value(number): return (0, 0, "")

            is_halogen = target_key in ["F", "CL", "BR", "I"]
            if not is_halogen:
                if number > 3000: return (0, 0, "")
                if number in [2.0, 5.0, 8.0, 10.0, 50.0]: return (0, 0, "")

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
    if "cti" in txt or "centre testing" in txt or "华测检测" in txt: return "CTI"
    if "tuv" in txt: return "TUV"
    return "OTHERS"

# --- 3. 核心：表格識別 ---

def identify_columns_by_company(table, company):
    item_idx = -1
    result_idx = -1
    mdl_idx = -1
    limit_idx = -1
    cas_idx = -1
    
    max_scan_rows = min(5, len(table))
    
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if "test item" in txt or "tested item" in txt or "测试项目" in txt or "substance name" in txt:
                if item_idx == -1: item_idx = c_idx
    
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            if "mdl" in txt or "loq" in txt or "检出限" in txt:
                mdl_idx = c_idx
            if "limit" in txt or "限值" in txt:
                limit_idx = c_idx
                continue
            if "cas" in txt:
                cas_idx = c_idx
                continue
            if "unit" in txt or "method" in txt: continue

            if "result" in txt or "结果" in txt or re.search(r"\b(no\.|00[1-9])", txt) or "claimed" in txt:
                 if result_idx == -1: result_idx = c_idx

    if result_idx == mdl_idx and result_idx != -1: result_idx = -1
    if result_idx == limit_idx and result_idx != -1: result_idx = -1
    if result_idx == cas_idx and result_idx != -1: result_idx = -1

    if item_idx == -1: item_idx = 0
    
    # Fallback Logic:
    # 如果找不到明確的 Result，且不是 Limit/MDL/CAS，則嘗試向左推
    if result_idx == -1 and len(table[0]) > 2: 
        candidate_idx = len(table[0]) - 1
        while candidate_idx >= 0:
            if candidate_idx != mdl_idx and candidate_idx != limit_idx and candidate_idx != cas_idx:
                result_idx = candidate_idx
                break
            candidate_idx -= 1

    return item_idx, result_idx, mdl_idx, limit_idx, cas_idx

# --- 4. 核心：文字模式 ---

def parse_text_lines(text, data_pool, file_group_data, filename, found_elements):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        line_lower = line_clean.lower()
        if "test method" in line_lower or "reference to" in line_lower or "determination of" in line_lower: continue
        if "directive" in line_lower and "2011/65" in line_lower: continue
        if "remark" in line_lower or "note" in line_lower: continue 

        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            if key in found_elements: continue # 如果已在表格找到，直接跳過文字掃描

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
                
                # v36.3: 標記 is_text_mode=True
                priority = parse_value_priority(part, target_key=matched_simple, is_text_mode=True)
                if priority[0] > 0:
                    found_val = part
                    break
            
            if found_val:
                priority = parse_value_priority(found_val, target_key=matched_simple, is_text_mode=True)
                if matched_simple:
                    # 標記 source=1 (Text)
                    data_pool[matched_simple].append({"priority": priority, "filename": filename, "source": 1})
                elif matched_group:
                    file_group_data[matched_group].append(priority)

# --- 主程式 ---

def process_files(files):
    # data_pool 結構更新：包含 source (2=Table, 1=Text)
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    
    # 追蹤器用於決定檔名，Pb 優先
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
                    # PFAS Summary 視為 Table 等級的權威 (4)
                    data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename, "source": 2})

                # --- 表格模式 ---
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx, mdl_idx, limit_idx, cas_idx = identify_columns_by_company(table, company)
                        if item_idx == -1: continue 

                        for row in table:
                            clean_row = [clean_text(cell) for cell in row]
                            if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
                            
                            item_name = clean_row[item_idx]
                            item_name_lower = item_name.lower()
                            if "test item" in item_name_lower or "result" in item_name_lower or "directive" in item_name_lower: continue
                            if "method" in item_name_lower or "remark" in item_name_lower or "note" in item_name_lower: continue

                            result_cell = ""
                            if result_idx != -1 and result_idx < len(clean_row):
                                result_cell = clean_row[result_idx]
                            
                            if not result_cell:
                                for col_idx, cell in enumerate(clean_row):
                                    if col_idx in [limit_idx, mdl_idx, cas_idx]: continue
                                    if "n.d." in cell.lower() or "not detected" in cell.lower() or "未检出" in cell.lower():
                                        result_cell = cell
                                        break
                                    if re.match(r"^\d+(\.\d+)?$", clean_text(cell)):
                                         if not is_suspicious_limit_value(cell):
                                            result_cell = cell

                            current_key = None
                            for k, v in SIMPLE_KEYWORDS.items():
                                for kw in v:
                                    if kw in item_name or kw.lower() in item_name.lower():
                                        current_key = k
                                        break
                                if current_key: break

                            priority = parse_value_priority(result_cell, target_key=current_key, is_text_mode=False)
                            if priority[0] == 0: continue
                            
                            # Simple Keywords
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw in item_name or kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and ("related" in item_name.lower() or "derivative" in item_name.lower()): continue
                                        
                                        # 標記 source=2 (Table)
                                        data_pool[target_key].append({"priority": priority, "filename": filename, "source": 2})
                                        found_elements_in_table.add(target_key)
                                        
                                        score, val, _ = priority
                                        if score > global_tracker[target_key]["max_score"]:
                                            global_tracker[target_key]["max_score"] = score
                                            global_tracker[target_key]["max_value"] = val
                                            global_tracker[target_key]["filename"] = filename
                                        elif score == global_tracker[target_key]["max_score"] and val > global_tracker[target_key]["max_value"]:
                                            global_tracker[target_key]["max_value"] = val
                                            global_tracker[target_key]["filename"] = filename
                                        break
                            
                            # Group Keywords
                            for group_key, keywords in GROUP_KEYWORDS.items():
                                for kw in keywords:
                                    if kw in item_name or kw.lower() in item_name.lower():
                                        file_group_data[group_key].append(priority)
                                        break
                
                # --- 文字模式 ---
                parse_text_lines(full_text_content, data_pool, file_group_data, filename, found_elements_in_table)
                
                # 更新 Tracker (for filename)
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

            # 結算 Group
            for group_key, values in file_group_data.items():
                if values:
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    # Group 通常來自表格，暫定 source=2
                    data_pool[group_key].append({
                        "priority": best_in_file,
                        "filename": filename,
                        "source": 2 
                    })

        except Exception as e:
            st.warning(f"⚠️ 檔案 {filename} 解析異常: {e}")
        
        progress_bar.progress((i + 1) / len(files))

    # --- 最終聚合 (v36.3 關鍵邏輯: Source 權重) ---
    final_row = {}
    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
        
        # 排序權重：Source (Table=2 > Text=1) -> Priority (Val>ND) -> Value -> String
        best_record = sorted(candidates, key=lambda x: (x.get('source', 0), x['priority'][0], x['priority'][1]), reverse=True)[0]
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

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS/CTI 報告聚合工具 v36.3", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v36.3 權威修正版)")
st.error("🛠️ v36.3：修正 PFOS (25)、I (19) 等雜訊。引入「表格權威機制」：若元素在表格中已找到，將強制忽略該元素的所有文字模式雜訊，並修正 Halogen 報告的抓取邏輯。")

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
