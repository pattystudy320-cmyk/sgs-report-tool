import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 定義欄位與關鍵字 ---
KEYWORD_MAP = {
    "Pb": ["Lead", "鉛", "Pb"],
    "Cd": ["Cadmium", "鎘", "Cd"],
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI"],
    "PBB": ["Sum of PBBs", "多溴聯苯總和", "PBBs", "Polybrominated Biphenyls"],
    "PBDE": ["Sum of PBDEs", "多溴聯苯醚總和", "PBDEs", "Polybrominated Diphenyl Ethers"],
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

# 最終輸出的欄位順序 (已移除 "單位")
OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "F", "CL", "BR", "I", 
    "日期", "檔案名稱"
]

# --- 2. 輔助功能 ---

def clean_text(text):
    """清理文字"""
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def extract_date_from_text(text):
    """
    日期抓取：支援 Date, 日期, Issue Date
    """
    text = clean_text(text)
    
    # 針對 Issue Date 或是 Date 做寬鬆匹配
    # 尋找關鍵字後面的日期格式 (06-Jan-2025 或 2025/01/06)
    date_patterns = [
        r"(?:Date|日期|Issue\s*Date).*?([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})", # 06-Jan-2025
        r"(?:Date|日期|Issue\s*Date).*?([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})" # 2025/01/06
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                # 嘗試解析第一種格式
                if "-" in match.group(1) and len(match.groups()) == 1:
                    return datetime.strptime(match.group(1), "%d-%b-%Y")
                # 嘗試解析第二種格式 (年/月/日)
                elif len(match.groups()) == 3:
                    return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except:
                continue
    return None

def parse_value_priority(value_str):
    """決定數值優先級"""
    # 清洗掉常見單位，只留數值
    val = clean_text(value_str).replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

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

def identify_columns(header_row):
    """識別 Result 和 Unit 的位置"""
    item_idx = -1
    result_idx = -1
    unit_idx = -1
    
    for i, cell in enumerate(header_row):
        txt = clean_text(cell).lower()
        if "test item" in txt or "tested item" in txt or "測試項目" in txt: item_idx = i
        if "result" in txt or "結果" in txt: result_idx = i
        if "unit" in txt or "單位" in txt: unit_idx = i
            
    return item_idx, result_idx, unit_idx

def process_files(files):
    data_pool = {key: [] for key in KEYWORD_MAP.keys()}
    all_dates = []
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        current_date = None
        
        try:
            with pdfplumber.open(file) as pdf:
                # 1. 抓日期 (第一頁)
                first_page_text = pdf.pages[0].extract_text()
                current_date = extract_date_from_text(first_page_text)
                if current_date:
                    all_dates.append((current_date, filename))

                # 2. 抓表格
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        header_row = table[0]
                        item_idx, result_idx, unit_idx = identify_columns(header_row)
                        
                        for row_idx, row in enumerate(table):
                            if row_idx == 0: continue
                            clean_row = [clean_text(cell) for cell in row]
                            if not any(clean_row): continue
                            
                            # 找測項
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            if "test item" in item_name.lower() or "測試項目" in item_name: continue

                            # 找結果
                            result = ""
                            # A. 優先用表頭定位
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            # B. 備援：特徵搜尋 (找 n.d. 或數字)
                            if not result:
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if "n.d." in c_lower or "negative" in c_lower or re.search(r"^\d+(\.\d+)?$", cell):
                                        result = cell
                                        break

                            # 匹配關鍵字
                            for target_key, keywords in KEYWORD_MAP.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        priority = parse_value_priority(result)
                                        data_pool[target_key].append({
                                            "priority": priority,
                                            "filename": filename,
                                            "date": current_date
                                        })
                                        break 
                                    
        except Exception as e:
            st.warning(f"檔案 {filename} 解析異常: {e}")

        progress_bar.progress((i + 1) / len(files))

    # --- 4. 聚合 ---
    final_row = {}
    max_val_filename = "" 
    global_max_score = -1

    for key in KEYWORD_MAP.keys():
        candidates = data_pool[key]
        if not candidates:
            final_row[key] = "" 
            continue
            
        # 排序取最優 (有數值 > Negative > n.d.)
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]
        
        # 判斷最大值檔案
        if best_record['priority'][0] > global_max_score:
            global_max_score = best_record['priority'][0]
            max_val_filename = best_record['filename']
        elif best_record['priority'][0] == 3 and global_max_score == 3:
             max_val_filename = best_record['filename']

    # 日期處理
    final_date_str = ""
    latest_file_name_by_date = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
        latest_file_name_by_date = latest_date_record[1]
    
    final_row["日期"] = final_date_str
    
    if global_max_score == 3: 
        final_row["檔案名稱"] = max_val_filename
    else:
        final_row["檔案名稱"] = latest_file_name_by_date if latest_file_name_by_date else (files[0].name if files else "")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v5.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v5.0 精簡版)")
st.info("💡 更新：移除單位欄位、增強日期抓取 (支援 Issue Date)")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="Report_Summary_v5.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
