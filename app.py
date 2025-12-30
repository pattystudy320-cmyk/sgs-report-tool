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
    "PFOS": ["Perfluorooctane sulfonates", "Perfluorooctane sulfonate", "Perfluorooctane sulfonic acid", "全氟辛烷磺酸"],
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
        "Decabromobiphenyl", 
        "Monobrominated", "Dibrominated", "Tribrominated", 
        "Tetrabrominated", "Pentabrominated", "Hexabrominated", 
        "Heptabrominated", "Octabrominated", "Nonabrominated", 
        "Decabrominated", "bromobiphenyl"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和",
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
    text = clean_text(text)
    patterns = [
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])", 
        r"(0?[1-9]|[12][0-9]|3[01])\s*[-/]\s*([a-zA-Z]{3})\s*[-/]\s*(20\d{2})", 
        r"([a-zA-Z]{3})\.?\s+(0?[1-9]|[12][0-9]|3[01])[,\s]+\s*(20\d{2})" 
    ]
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                full_match = match.group(0)
                clean_str = full_match.replace(".", " ").replace(",", " ").replace("-", " ").replace("/", " ")
                clean_str = " ".join(clean_str.split())
                for fmt in ["%Y %m %d", "%d %b %Y", "%b %d %Y"]:
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
            return (3, number, num_match.group(1))
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

            # 結果欄位判斷
            if company == "SGS":
                if ("result" in txt or "結果" in txt or re.search(r"00[1-9]", txt) or 
                    re.search(r"^[a-z]?\s*-?\s*\d+$", txt) or "no." in txt):
                    if "cas" not in txt and "method" not in txt and "limit" not in txt:
                        if result_idx == -1: result_idx = c_idx
            elif company == "INTERTEK":
                if "result" in txt or "green" in txt or "submitted" in txt:
                    if result_idx == -1: result_idx = c_idx
            else: 
                if "result" in txt or "結果" in txt or re.search(r"00[1-9]", txt):
                    if result_idx == -1: result_idx = c_idx

    # 智慧推斷
    if result_idx == -1:
        if company == "SGS":
            if mdl_idx != -1 and mdl_idx + 1 < len(table[0]):
                result_idx = mdl_idx + 1
    
    is_reference_table = False
    if result_idx == -1:
        if "restricted substances" in full_header_text or "group name" in full_header_text or "substance name" in full_header_text:
            is_reference_table = True
        if company == "INTERTEK" and "limits" in full_header_text:
            is_reference_table = True
        if item_idx == -1:
            is_reference_table = True

    return item_idx, result_idx, is_reference_table

# --- 4. 核心：文字模式 (SGS 救援引擎) ---

def parse_text_lines(text, data_pool, company):
    """
    針對抓不到表格的 PDF，進行逐行文字掃描
    """
    lines = text.split('\n')
    current_item = ""
    
    # 針對 SGS 的文字型報告，常見格式：
    # Item(s) Limit Unit MDL 001
    # Cadmium (Cd) 100 mg/kg 2 ND
    
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        # 1. 嘗試配對測項關鍵字
        # 檢查這一行是否有測項名稱
        matched_key = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            for kw in keywords:
                if kw in line_clean and "test item" not in line_clean.lower():
                    matched_key = key
                    break
            if matched_key: break
            
        if not matched_key:
            # 檢查 Group Keywords (PBB/PBDE)
            for group_key, keywords in GROUP_KEYWORDS.items():
                for kw in keywords:
                    if kw in line_clean:
                        matched_key = group_key
                        break
                if matched_key: break
        
        if matched_key:
            # 這一行有測項，嘗試找後面的數值
            # 策略：找行尾的 "ND" 或 數字
            # 去除 (Limit 1000) (MDL 2) 等干擾
            
            # 簡單分割
            parts = line_clean.split()
            if len(parts) < 2: continue
            
            # 從後面往前找，找到第一個像結果的東西
            found_val = ""
            for part in reversed(parts):
                p_lower = part.lower()
                # 忽略單位、MDL、Limit
                if p_lower in ["mg/kg", "ppm", "2", "5", "10", "50", "100", "1000", "0.1", "-", "---"]:
                    continue
                
                # 檢查是否為 ND
                if "nd" in p_lower:
                    found_val = "N.D."
                    break
                
                # 檢查是否為數字 (且不是 Limit/MDL)
                if re.match(r"^\d+(\.\d+)?$", part):
                    # 再次確認不是 Limit (SGS 常見 Limit: 100, 1000)
                    val_float = float(part)
                    if val_float not in [100.0, 1000.0, 50.0, 10.0, 5.0, 2.0]:
                        found_val = part
                        break
            
            if found_val:
                # 存入 data_pool
                # 為了避免與表格資料重複，這裡設一個較低的 priority (2.5)，
                # 如果表格有抓到 (3.0)，會覆蓋這個
                priority_score = 3 if re.match(r"^\d", found_val) else 1
                
                # 如果是 Group (PBB/PBDE)，需要特殊處理，這裡簡化為：只存入 Simple
                if matched_key in SIMPLE_KEYWORDS:
                    data_pool[matched_key].append({
                        "priority": (priority_score, 0, found_val),
                        "filename": "text_mode" # 標記
                    })
                # Group 暫不支援文字模式 (因為太複雜且通常有表格)，
                # 但如果是 "Sum of PBBs" 這種總結行，可以視為 Simple 處理

# --- 主程式 ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    pb_tracker = {"max_score": -1, "max_value": -1.0, "filenames": []}
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        
        try:
            with pdfplumber.open(file) as pdf:
                file_dates = []
                first_few_pages_text = ""
                full_text_content = "" # 用於文字模式
                
                # 1. 前置掃描
                for p_idx in range(len(pdf.pages)):
                    page = pdf.pages[p_idx]
                    page_txt = page.extract_text() or ""
                    full_text_content += page_txt + "\n"
                    
                    if p_idx < 3:
                        first_few_pages_text += page_txt
                        d = extract_date_from_text(page_txt)
                        if d: file_dates.append(d)
                
                if file_dates: all_dates.append((max(file_dates), filename))
                company = identify_company(first_few_pages_text)
                
                if check_pfas_in_summary(first_few_pages_text):
                    data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

                # 2. 引擎 A: 表格模式
                has_table_data = False
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx, is_skip_table = identify_columns_by_company(table, company)
                        if is_skip_table: continue 

                        # 表頭記憶
                        # (簡化：若無記憶，每次重找)
                        
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
                            
                            # 備援
                            if not result:
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    if "nd" in c_lower or "n.d." in c_lower or "negative" in c_lower:
                                        result = cell
                                        break
                                    if re.search(r"^\d+(\.\d+)?$", cell):
                                        if float(cell) in [1000, 100, 50]: continue 
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            has_table_data = True # 標記有抓到資料

                            # Simple
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower(): continue 
                                        data_pool[target_key].append({"priority": priority, "filename": filename})
                                        
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
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        file_group_data[group_key].append(priority)
                                        break
                
                # 3. 引擎 B: 文字模式 (如果表格模式沒抓到關鍵數據)
                # 檢查 Pb 是否為空，如果 Pb 沒抓到，很有可能這份報告是「偽表格」
                pb_data_count = len([d for d in data_pool["Pb"] if d['filename'] == filename])
                
                if pb_data_count == 0 and company == "SGS":
                    # 啟動文字解析
                    parse_text_lines(full_text_content, data_pool, company)
                    # 這裡只做 Simple Keywords 的補救，Group 較難用文字模式處理
            
            # 檔案結算 (PBB/PBDE) - 來自表格模式的資料
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
st.set_page_config(page_title="SGS 報告聚合工具 v32.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v32.0 雙引擎旗艦版)")
st.info("💡 v32.0：導入雙引擎機制，若表格識別失敗，自動切換至文字掃描模式，專門解決 SGS 偽表格報告。")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v32.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
