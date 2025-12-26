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
        "PBBs", "Polybrominated Biphenyls", "Sum of PBBs", "多溴聯苯總和",
        "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", "bromobiphenyl"
    ],
    "PBDE": [
        "PBDEs", "Polybrominated Diphenyl Ethers", "Sum of PBDEs", "多溴聯苯醚總和",
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
    強力日期抓取
    """
    text = clean_text(text)
    # 支援格式：
    # 06-Jan-2025
    # 2025/01/06, 2025.01.06, 2025-01-06
    # Jan 06, 2025
    patterns = [
        r"([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})", 
        r"([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})",
        r"([a-zA-Z]{3})\s+([0-9]{1,2})[,]\s+([0-9]{4})"
    ]
    
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                groups = match.groups()
                if len(groups) == 1 and "-" in groups[0]: # 06-Jan-2025
                    dt = datetime.strptime(groups[0], "%d-%b-%Y")
                elif len(groups) == 3:
                    if groups[0].isdigit(): # 2025/01/06
                        dt = datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                    else: # Jan 06, 2025
                        dt = datetime.strptime(f"{groups[1]}-{groups[0]}-{groups[2]}", "%d-%b-%Y")
                
                if dt: found_dates.append(dt)
            except: continue
            
    # 如果抓到多個日期，回傳最大(最新)的那個
    if found_dates:
        return max(found_dates)
    return None

def parse_value_priority(value_str):
    val = clean_text(value_str).replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    if val_lower in ["result", "limit", "mdl", "loq", "unit", "method", "004", "no.1", "---"]: return (0, 0, "")

    if "n.d." in val_lower or "nd" == val_lower or "<" in val_lower: return (1, 0, "n.d.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "Negative")
    
    num_match = re.search(r"([\d\.]+)", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, val)
        except: pass
            
    return (0, 0, val)

# --- 3. 核心：動態欄位識別 ---

def check_pfas_trigger(full_text):
    for phrase in PFAS_TRIGGER_PHRASES:
        if phrase.lower() in full_text.lower():
            return True
    return False

def identify_columns(header_row):
    item_idx = -1
    result_idx = -1
    
    for i, cell in enumerate(header_row):
        txt = clean_text(cell).lower()
        if "test item" in txt or "tested item" in txt or "測試項目" in txt: item_idx = i
        if "result" in txt or "結果" in txt: result_idx = i
            
    return item_idx, result_idx

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    
    # 專門用來追蹤 Pb 的最大值檔案
    pb_tracker = {
        "max_score": -1,
        "max_value": -1,
        "filename": ""
    }
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        full_text_content = ""

        try:
            with pdfplumber.open(file) as pdf:
                # 1. 抓日期 (掃描前兩頁)
                date_found = None
                for p_idx in range(min(2, len(pdf.pages))):
                    page_txt = pdf.pages[p_idx].extract_text()
                    if page_txt:
                        full_text_content += page_txt
                        d = extract_date_from_text(page_txt)
                        if d and not date_found: 
                            date_found = d
                            
                if date_found:
                    all_dates.append((date_found, filename))
                
                # 補讀其他頁文字供 PFAS 判斷
                for p in pdf.pages[2:]:
                    full_text_content += (p.extract_text() or "")

                pfas_active = check_pfas_trigger(full_text_content)

                # 2. 抓表格
                
                # ★ 表頭記憶變數：若遇到沒表頭的表格，沿用上一個的設定
                last_result_idx = -1 
                last_item_idx = 0

                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        header_row = table[0]
                        item_idx, result_idx = identify_columns(header_row)
                        
                        # ★ 表頭記憶邏輯
                        if result_idx != -1:
                            last_result_idx = result_idx
                            last_item_idx = item_idx if item_idx != -1 else 0
                        else:
                            # 沒抓到表頭，使用上一次成功的設定
                            if last_result_idx != -1:
                                result_idx = last_result_idx
                                item_idx = last_item_idx
                        
                        for row_idx, row in enumerate(table):
                            # 若這行是表頭，跳過
                            clean_row = [clean_text(cell) for cell in row]
                            row_text_joined = "".join(clean_row).lower()
                            if "test item" in row_text_joined or "result" in row_text_joined:
                                continue
                                
                            if not any(clean_row): continue
                            
                            # 找測項
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            # 找結果
                            result = ""
                            # A. 優先用定位
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            # B. 備援
                            if not result:
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    if "n.d." in c_lower or "negative" in c_lower or re.search(r"^\d+(\.\d+)?$", cell):
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            # --- A. Simple (含 Pb 追蹤) ---
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower(): continue 
                                        
                                        data_pool[target_key].append({
                                            "priority": priority,
                                            "filename": filename
                                        })
                                        
                                        # ★ Pb 檔案追蹤邏輯 ★
                                        if target_key == "Pb":
                                            # 如果分數更高 (有值 > n.d.)
                                            if priority[0] > pb_tracker["max_score"]:
                                                pb_tracker["max_score"] = priority[0]
                                                pb_tracker["max_value"] = priority[1]
                                                pb_tracker["filename"] = filename
                                            # 如果都是數值，比大小
                                            elif priority[0] == 3 and priority[1] > pb_tracker["max_value"]:
                                                pb_tracker["max_value"] = priority[1]
                                                pb_tracker["filename"] = filename
                                                
                                        break

                            # --- B. Group ---
                            for group_key, keywords in GROUP_KEYWORDS.items():
                                if group_key == "PFAS" and not pfas_active: continue

                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if group_key == "PFAS" and "pfos" in item_name.lower() and "related" not in item_name.lower():
                                            continue
                                        
                                        file_group_data[group_key].append(priority)
                                        break
            
            # --- 檔案結算 (Group) ---
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

    # --- 4. 聚合 ---
    final_row = {}

    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
            
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]

    # 日期處理 (取最新)
    final_date_str = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
    
    final_row["日期"] = final_date_str
    
    # ★ 檔案名稱邏輯：顯示 Pb 值最大的那個檔案 ★
    # 如果完全沒抓到 Pb (極少見)，則退回使用日期最新的檔案
    if pb_tracker["filename"]:
        final_row["檔案名稱"] = pb_tracker["filename"]
    else:
        # Fallback
        if all_dates:
            final_row["檔案名稱"] = sorted(all_dates, key=lambda x: x[0], reverse=True)[0][1]
        else:
            final_row["檔案名稱"] = files[0].name if files else ""

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v10.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v10.0 完美版)")
st.info("💡 v10.0 更新：修復 PBB/PBDE 表格抓取(表頭記憶)、指定 Pb 最大值檔案名稱、增強日期辨識。")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v10.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
