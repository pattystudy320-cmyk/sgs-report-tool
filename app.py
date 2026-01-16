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

# v35.4: 定義常見的測試方法編號與干擾數字 (黑名單)
BLACKLIST_NUMBERS = [
    6476, 3052, 14582, 62321, 17025, 2011, 2015, 2021, 2022, 2023, 2024, 2025
]

# --- 2. 輔助功能 (v35.4 強力過濾版) ---

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
        # v35.4: 擴大 MDL 數字的防禦範圍 (針對 Cr6+ = 8 或 10)
        if n in [1000.0, 100.0, 50.0, 10.0, 8.0, 5.0, 2.0]: return True
        return False
    except: return False

def parse_value_priority(value_str, target_key=None):
    """
    v35.4 核心解析函數
    target_key: 正在尋找的元素名稱 (例如 'Pb', 'BBP', 'F')，用於區分鹵素與非鹵素的過濾邏輯
    """
    raw_val = clean_text(value_str)
    if "(" in raw_val: raw_val = raw_val.split("(")[0].strip()
    
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    # --- 過濾器 1: 關鍵字排除 ---
    # v35.4: 新增中文日期關鍵字過濾 (解決 DEHP 抓到日期說明)
    if any(x in val_lower for x in ["iec", "iso", "epa", "gb/t", "directive", "annex", "mdl", "loq", "limit", "result", "unit", "method", "reference", "determination", "conclusion", "pass", "fail", "requirement", "---", "note", "remark"]):
        return (0, 0, "")
    
    if any(x in val for x in ["年", "月", "日", "开始", "执行", "standard"]): 
        return (0, 0, "")

    if ":" in val: return (0, 0, "") # 排除帶冒號的 (如標準號)
    if "/" in val and "n/a" not in val_lower: return (0, 0, "") # 排除帶斜線的 (如法規)

    # --- 過濾器 2: N.D. / Negative ---
    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower or "not detected" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "NEGATIVE")
    
    # --- 過濾器 3: 純數字檢查 ---
    # 排除 CAS No (例如 7439-92-1)
    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    if re.search(r"\d{4,}-\d+", val): return (0, 0, "")

    num_match = re.search(r"^([\d\.]+)\s*(.*)$", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            
            # v35.4: 黑名單攔截 (針對 6476, 3052 等)
            if int(number) in BLACKLIST_NUMBERS:
                return (0, 0, "")

            is_halogen = target_key in ["F", "CL", "BR", "I"]
            
            # v35.4: 非鹵素項目的嚴格過濾
            if not is_halogen:
                # 如果是非鹵素，且數值 > 3000 (很少有這麼高的汙染，通常是標準號)
                if number > 3000: return (0, 0, "")
                # 如果是非鹵素，且數值剛好是 2, 5, 8, 10, 50 (極可能是 MDL)
                if number in [2.0, 5.0, 8.0, 10.0, 50.0]:
                    # 這裡比較難判斷，先標記為較低優先級，或者依賴外部 MDL 欄位判斷
                    # 為求保險，若文字模式抓到這些整數，暫時視為雜訊 (通常結果會有 N.D. 覆蓋)
                    pass 

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
    mdl_idx = -1 # v35.4: 新增 MDL 欄位定位
    
    max_scan_rows = min(5, len(table))
    
    # 1. 找 Item 欄
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if "test item" in txt or "tested item" in txt or "測試項目" in txt or "substance name" in txt:
                if item_idx == -1: item_idx = c_idx
    
    # 2. 找 Result 和 MDL 欄
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            # 定位 MDL/Limit (避開這些)
            if "mdl" in txt or "loq" in txt or "dl" in txt:
                mdl_idx = c_idx
            
            if "limit" in txt or "unit" in txt or "method" in txt or "cas" in txt: continue

            if "result" in txt or "結果" in txt or re.search(r"\b(no\.|00[1-9])", txt) or "claimed" in txt:
                 if result_idx == -1: result_idx = c_idx

    # v35.4: 安全防護 - 如果找到的 Result 欄位剛好是 MDL 欄位，強制重找
    if result_idx == mdl_idx and result_idx != -1:
        result_idx = -1 # 重置，嘗試找最後一欄

    if item_idx == -1: item_idx = 0
    # Fallback
    if result_idx == -1 and len(table[0]) > 2: 
        # 假設最後一欄是結果，但不能是 MDL
        candidate_idx = len(table[0]) - 1
        if candidate_idx != mdl_idx:
            result_idx = candidate_idx
        else:
            result_idx = candidate_idx - 1 # 往前一格

    return item_idx, result_idx, mdl_idx

# --- 4. 核心：文字模式 ---

def parse_text_lines(text, data_pool, file_group_data, filename):
    lines = text.split('\n')
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        line_lower = line_clean.lower()
        # v35.4: 強力過濾文字行
        if "test method" in line_lower or "reference to" in line_lower or "determination of" in line_lower: continue
        if "directive" in line_lower and "2011/65" in line_lower: continue
        if "remark" in line_lower or "note" in line_lower: continue # 過濾備註行

        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
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
                
                # 傳入 matched_simple 作為 target_key 以區分鹵素
                priority = parse_value_priority(part, target_key=matched_simple)
                
                if priority[0] > 0:
                    found_val = part
                    break
            
            if found_val:
                priority = parse_value_priority(found_val, target_key=matched_simple)
                if matched_simple:
                    data_pool[matched_simple].append({"priority": priority, "filename": filename})
                elif matched_group:
                    file_group_data[matched_group].append(priority)

# --- 主程式 ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    
    # 全局追蹤器
    global_tracker = {key: {"max_score": -1, "max_value": -1.0, "filename": ""} for key in SIMPLE_KEYWORDS.keys()}
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        
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
                    data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

                # 表格模式
                has_table_data = False
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx, mdl_idx = identify_columns_by_company(table, company)
                        if item_idx == -1: continue 

                        for row in table:
                            clean_row = [clean_text(cell) for cell in row]
                            if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
                            
                            item_name = clean_row[item_idx]
                            item_name_lower = item_name.lower()
                            if "test item" in item_name_lower or "result" in item_name_lower or "directive" in item_name_lower: continue
                            
                            # v35.4: 安全過濾 - 如果這一行看起來像方法描述或備註
                            if "method" in item_name_lower or "remark" in item_name_lower or "note" in item_name_lower: continue

                            result_cell = ""
                            if result_idx != -1 and result_idx < len(clean_row):
                                result_cell = clean_row[result_idx]
                            
                            # Fallback
                            if not result_cell:
                                for cell in clean_row:
                                    if "n.d." in cell.lower() or "not detected" in cell.lower():
                                        result_cell = cell
                                        break
                                    # 嘗試找數字 (排除疑似 MDL 的小整數)
                                    if re.match(r"^\d+(\.\d+)?$", clean_text(cell)):
                                         # 如果這個數字出現在 MDL 欄位，絕對不要抓
                                         current_col_idx = clean_row.index(cell)
                                         if current_col_idx == mdl_idx: continue
                                         
                                         if not is_suspicious_limit_value(cell):
                                            result_cell = cell

                            # 識別 Item Key 傳入解析函數
                            current_key = None
                            for k, v in SIMPLE_KEYWORDS.items():
                                for kw in v:
                                    if kw.lower() in item_name.lower():
                                        current_key = k
                                        break
                                if current_key: break

                            priority = parse_value_priority(result_cell, target_key=current_key)
                            if priority[0] == 0: continue
                            
                            has_table_data = True

                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and ("related" in item_name.lower() or "derivative" in item_name.lower()): continue
                                        
                                        data_pool[target_key].append({"priority": priority, "filename": filename})
                                        
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
                                    if kw.lower() in item_name.lower():
                                        file_group_data[group_key].append(priority)
                                        break
                
                # 文字模式
                parse_text_lines(full_text_content, data_pool, file_group_data, filename)
                
                # 更新全局 Tracker
                for k in SIMPLE_KEYWORDS.keys():
                    for d in data_pool[k]:
                         if d['filename'] == filename:
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
    
    # 檔名邏輯：若 Pb 沒抓到，嘗試用 Cd 或 PBB 的檔名，再沒有才用日期
    final_file = ""
    if global_tracker["Pb"]["filename"]:
        final_file = global_tracker["Pb"]["filename"]
    elif global_tracker["Cd"]["filename"]:
        final_file = global_tracker["Cd"]["filename"]
    else:
        final_file = latest_date_record[1] if all_dates else (files[0].name if files else "Unknown")
        
    final_row["檔案名稱"] = final_file

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS/Intertek 報告聚合工具 v35.4", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v35.4 終極修正版)")
st.error("🛠️ v35.4 修正：解決 DEHP 抓到中文日期、BBP 抓到 6476、Cr6+ 抓到 8 (MDL) 的問題。")

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
