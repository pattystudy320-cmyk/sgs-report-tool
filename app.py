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
    "PFOS": ["PFOS", "Perfluorooctane sulfonates", "Perfluorooctane sulfonate"],
    "F": ["Fluorine", "氟"],
    "CL": ["Chlorine", "氯"],
    "BR": ["Bromine", "溴"],
    "I": ["Iodine", "碘"]
}

GROUP_KEYWORDS = {
    "PBB": [
        "Polybrominated Biphenyls", "PBBs", "Sum of PBBs", "多溴聯苯總和",
        "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", "bromobiphenyl"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和",
        "Monobromodiphenyl ether", "Dibromodiphenyl ether", "Tribromodiphenyl ether",
        "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", "Hexabromodiphenyl ether",
        "Heptabromodiphenyl ether", "Octabromodiphenyl ether", "Nonabromodiphenyl ether",
        "Decabromodiphenyl ether", "bromodiphenyl ether"
    ],
    "PFAS": [
        "PFHxA", "PFOA", "PFNA", "PFDA", "PFUnDA", "PFDoDA", "PFTrDA", "PFTeDA",
        "FTOH", "FTA", "FTMAC", "FTS", "FTCA", "PFAS", "Perfluoro", "全氟"
    ]
}

PFAS_TRIGGER_PHRASES = [
    "Per- and Polyfluoroalkyl Substances",
    "PFHxA and its salts",
    "全氟/多氟烷基物質"
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

def extract_date_from_text(text):
    """
    v18.0: 增強日期格式 (允許空格)
    """
    text = clean_text(text)
    
    patterns = [
        # 2023/03/03
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])",
        # 06-Jan-2025 (允許空格: 06 - Jan - 2025)
        r"(0?[1-9]|[12][0-9]|3[01])\s*-\s*([a-zA-Z]{3})\s*-\s*(20\d{2})",
        # Mar 03, 2023
        r"([a-zA-Z]{3})\s+(0?[1-9]|[12][0-9]|3[01])[,]\s+(20\d{2})",
        # Dec. 26, 2024 (CTI)
        r"([a-zA-Z]{3})\.\s*(0?[1-9]|[12][0-9]|3[01])[,]\s+(20\d{2})"
    ]
    
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                full_match = match.group(0)
                # 移除干擾字元
                clean_str = full_match.replace(" ", "").replace(".", "").replace(",", "")
                
                for fmt in ["%Y/%m/%d", "%d-%b-%Y", "%b%d%Y"]:
                    try:
                        dt = datetime.strptime(clean_str.replace("-", "/").replace(".", "/"), fmt)
                        break
                    except: continue
                
                if dt and 2000 <= dt.year <= 2030: 
                    found_dates.append(dt)
            except: continue
            
    if found_dates:
        return max(found_dates)
    return None

def is_suspicious_limit_value(val):
    """數值防火牆：阻擋常見限值"""
    try:
        n = float(val)
        block_list = [1000.0, 100.0, 50.0] 
        if n in block_list: return True
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

    if is_suspicious_limit_value(val): return (0, 0, "") 

    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower: 
        return (1, 0, "n.d.")
    if "negative" in val_lower or "陰性" in val_lower: 
        return (2, 0, "Negative")
    
    num_match = re.search(r"([\d\.]+)", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, num_match.group(1))
        except: pass
            
    return (0, 0, val)

# --- 3. 核心：智慧欄位識別 ---

def check_pfas_trigger(full_text):
    for phrase in PFAS_TRIGGER_PHRASES:
        if phrase.lower() in full_text.lower(): return True
    return False

def identify_columns(table):
    """
    v18.0: 增強「參考表」識別，防止誤讀 PFAS 清單
    """
    item_idx = -1
    result_idx = -1
    
    max_scan_rows = min(3, len(table))
    full_header_text = ""
    for r in range(max_scan_rows):
        full_header_text += " ".join([str(c).lower() for c in table[r] if c]) + " "
    
    # ★ 1. 限值表過濾 ★
    if ("restricted substances" in full_header_text or "limits" in full_header_text) and \
       not any(x in full_header_text for x in ["result", "結果", "00", "no.", "green"]):
        return -1, -1, True # Skip

    # ★ 2. 參考清單過濾 (PFAS Remark) ★
    # 如果標題有 "Substance Name", "CAS No" 但完全沒有 "Result"
    if ("substance name" in full_header_text or "cas no" in full_header_text) and \
       not any(x in full_header_text for x in ["result", "結果", "00", "no.", "green"]):
        return -1, -1, True # Skip

    for r_idx in range(max_scan_rows):
        row = table[r_idx]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            if "test item" in txt or "tested item" in txt or "測試項目" in txt:
                if item_idx == -1: item_idx = c_idx
            
            if ("result" in txt or "結果" in txt or re.search(r"00[1-9]", txt) or 
                "no." in txt or "green" in txt or "submitted" in txt or "composite" in txt):
                if result_idx == -1: result_idx = c_idx

    return item_idx, result_idx, False

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    pb_tracker = {"max_score": -1, "max_value": -1.0, "filenames": []}
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        full_text_content = ""

        try:
            with pdfplumber.open(file) as pdf:
                # 抓日期
                file_dates = []
                for p_idx in range(min(3, len(pdf.pages))):
                    page_txt = pdf.pages[p_idx].extract_text()
                    if page_txt:
                        full_text_content += page_txt
                        d = extract_date_from_text(page_txt)
                        if d: file_dates.append(d)
                if file_dates: all_dates.append((max(file_dates), filename))
                
                for p in pdf.pages[3:]: full_text_content += (p.extract_text() or "")
                pfas_active = check_pfas_trigger(full_text_content)

                last_result_idx = -1 
                last_item_idx = 0

                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx, is_skip_table = identify_columns(table)
                        
                        if is_skip_table: continue # 跳過限值表/參考表

                        if result_idx != -1:
                            last_result_idx = result_idx
                            last_item_idx = item_idx if item_idx != -1 else 0
                        else:
                            # 只有當上一個表頭合理時才沿用，且當前表格不能長得像參考表
                            # 簡單防呆：若表格只有 3 欄，通常是參考表，不沿用結果欄位
                            if last_result_idx != -1 and len(table[0]) > 3:
                                result_idx = last_result_idx
                                item_idx = last_item_idx
                        
                        for row_idx, row in enumerate(table):
                            clean_row = [clean_text(cell) for cell in row]
                            row_txt = "".join(clean_row).lower()
                            if "test item" in row_txt or "result" in row_txt or "restricted" in row_txt: continue
                            if not any(clean_row): continue
                            
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            # ★ PVC 排除 (防止 Cl 誤抓) ★
                            if "pvc" in item_name.lower() or "polyvinyl" in item_name.lower():
                                continue

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
                                    if re.search(r"^\d+(\.\d+)?$", cell):
                                        if float(cell) in [1000, 100, 50, 25, 10, 5, 2]: continue
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            # Simple
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower(): continue 
                                        
                                        data_pool[target_key].append({
                                            "priority": priority,
                                            "filename": filename
                                        })
                                        
                                        if target_key == "Pb":
                                            current_score = priority[0]
                                            current_val = priority[1]
                                            if current_score > pb_tracker["max_score"]:
                                                pb_tracker["max_score"] = current_score
                                                pb_tracker["max_value"] = current_val
                                                pb_tracker["filenames"] = [filename]
                                            elif current_score == 3 and current_val > pb_tracker["max_value"]:
                                                pb_tracker["max_value"] = current_val
                                                pb_tracker["filenames"] = [filename]
                                            elif current_score == 3 and current_val == pb_tracker["max_value"]:
                                                if filename not in pb_tracker["filenames"]:
                                                    pb_tracker["filenames"].append(filename)
                                        break

                            # Group
                            for group_key, keywords in GROUP_KEYWORDS.items():
                                if group_key == "PFAS" and not pfas_active: continue
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if group_key == "PFAS" and "pfos" in item_name.lower() and "related" not in item_name.lower():
                                            continue
                                        file_group_data[group_key].append(priority)
                                        break
            
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

    # 日期與檔名
    final_date_str = ""
    latest_file = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
        latest_file = latest_date_record[1]
    
    final_row["日期"] = final_date_str
    
    if pb_tracker["filenames"]:
        final_row["檔案名稱"] = ", ".join(pb_tracker["filenames"])
    else:
        final_row["檔案名稱"] = latest_file if latest_file else (files[0].name if files else "")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v18.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v18.0)")
st.info("💡 v18.0：排除 PFAS 參考表誤讀(數值11)、修正 Cl 誤判 PVC Negative、增強日期格式。")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v18.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
