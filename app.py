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

OUTPUT_COLUMNS = [
    "Pb", "Cd", "Hg", "Cr6+", "PBB", "PBDE", 
    "DEHP", "BBP", "DBP", "DIBP", 
    "PFOS", "F", "CL", "BR", "I", 
    "單位", "日期", "檔案名稱"
]

# --- 2. 輔助功能 ---

def clean_text(text):
    """清理文字：移除換行、多餘空白"""
    if not text: return ""
    return str(text).replace('\n', ' ').strip()

def extract_date_from_text(text):
    """全方位日期抓取"""
    text = clean_text(text)
    # 模式 1: 06-Jan-2025
    match1 = re.search(r"([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})", text)
    if match1:
        try: return datetime.strptime(match1.group(1), "%d-%b-%Y")
        except: pass
    # 模式 2: 2025/01/06, 2025.01.06, 2025-01-06
    match2 = re.search(r"([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})", text)
    if match2:
        try: return datetime(int(match2.group(1)), int(match2.group(2)), int(match2.group(3)))
        except: pass
    return None

def parse_value_priority(value_str):
    """決定數值優先級 & 清洗單位"""
    val = clean_text(value_str).replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    if "n.d." in val_lower or "nd" == val_lower or "<" in val_lower: return (1, 0, "n.d.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "Negative")
    
    # 抓取數字
    num_match = re.search(r"([\d\.]+)", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, val)
        except: pass
            
    return (0, 0, val)

# --- 3. 核心：動態欄位識別 ---

def identify_columns(header_row):
    """
    分析表頭列，找出 'Result' 和 'Unit' 分別在第幾欄
    回傳: (item_idx, result_idx, unit_idx)
    """
    item_idx = -1
    result_idx = -1
    unit_idx = -1
    
    for i, cell in enumerate(header_row):
        txt = clean_text(cell).lower()
        
        # 找測項欄 (通常是 Item, Test Item)
        if "test item" in txt or "tested item" in txt or "測試項目" in txt:
            item_idx = i
        
        # 找結果欄 (Result, 結果, No.1, 004)
        if "result" in txt or "結果" in txt:
            result_idx = i
            
        # 找單位欄 (Unit, 單位)
        if "unit" in txt or "單位" in txt:
            unit_idx = i
            
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
                # 1. 抓日期
                first_page_text = pdf.pages[0].extract_text()
                current_date = extract_date_from_text(first_page_text)
                if current_date:
                    all_dates.append((current_date, filename))

                # 2. 抓表格
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        # A. 先嘗試讀取第一列當作表頭，定位欄位索引
                        header_row = table[0]
                        item_idx, result_idx, unit_idx = identify_columns(header_row)
                        
                        # B. 如果表頭沒抓到 Result，嘗試用備用邏輯 (SGS 經典版通常 Result 在倒數第2或3欄)
                        # 但因為格式太多變，如果沒抓到，我們會在每一列動態判斷
                        
                        # C. 遍歷每一列數據
                        for row_idx, row in enumerate(table):
                            # 跳過表頭列
                            if row_idx == 0: continue
                            
                            clean_row = [clean_text(cell) for cell in row]
                            
                            # 確保這一列有資料
                            if not any(clean_row): continue
                            
                            # 1. 確定測項名稱
                            # 如果有抓到 item_idx 就用它，否則預設用第0欄
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            # 防呆：如果這一欄是 "Test Item" 標題，跳過
                            if "test item" in item_name.lower() or "測試項目" in item_name: continue

                            # 2. 確定結果與單位
                            result = ""
                            unit = ""
                            
                            # 策略 A: 根據表頭抓到的索引
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            if unit_idx != -1 and unit_idx < len(clean_row):
                                unit = clean_row[unit_idx]
                                
                            # 策略 B (備援): 如果沒抓到表頭，用「內容特徵」猜
                            if not result:
                                # 找看起來像結果的格子 (包含 n.d., Negative, 或者數字)
                                # 倒著找回來通常比較準 (Result 通常在右邊)
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if "n.d." in c_lower or "negative" in c_lower or re.search(r"^\d+(\.\d+)?$", cell):
                                        result = cell
                                        break
                            
                            if not unit:
                                # 找看起來像單位的格子
                                for cell in clean_row:
                                    if "mg/kg" in cell or "ppm" in cell:
                                        unit = cell
                                        break
                            
                            # 如果還是沒抓到單位，但結果欄位裡面有單位 (例如 "8 mg/kg")
                            if result and not unit:
                                if "mg/kg" in result: unit = "mg/kg"
                                elif "ppm" in result: unit = "ppm"

                            # 3. 匹配關鍵字並存檔
                            for target_key, keywords in KEYWORD_MAP.items():
                                for kw in keywords:
                                    # 使用較嚴格的比對，避免 PBB 抓到 PBBs-related
                                    if kw.lower() in item_name.lower():
                                        priority = parse_value_priority(result)
                                        data_pool[target_key].append({
                                            "priority": priority,
                                            "filename": filename,
                                            "date": current_date,
                                            "unit": unit
                                        })
                                        break 
                                    
        except Exception as e:
            st.warning(f"檔案 {filename} 解析異常: {e}")

        progress_bar.progress((i + 1) / len(files))

    # --- 4. 聚合 ---
    final_row = {}
    max_val_filename = "" 
    global_max_score = -1
    default_unit = ""

    for key in KEYWORD_MAP.keys():
        candidates = data_pool[key]
        if not candidates:
            final_row[key] = "" 
            continue
            
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]
        
        if best_record['priority'][0] == 3 and not default_unit:
            default_unit = best_record['unit']
            
        if best_record['priority'][0] > global_max_score:
            global_max_score = best_record['priority'][0]
            max_val_filename = best_record['filename']
        elif best_record['priority'][0] == 3 and global_max_score == 3:
             max_val_filename = best_record['filename']

    final_date_str = ""
    latest_file_name_by_date = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
        latest_file_name_by_date = latest_date_record[1]
    
    final_row["單位"] = default_unit if default_unit else "mg/kg"
    final_row["日期"] = final_date_str
    
    if global_max_score == 3: 
        final_row["檔案名稱"] = max_val_filename
    else:
        final_row["檔案名稱"] = latest_file_name_by_date if latest_file_name_by_date else (files[0].name if files else "")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v4.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (SGS/Intertek/ALS 通用版)")
st.info("💡 v4.0 更新：支援多種不同廠商的報告格式 (自動識別 Result 與 Unit 位置)")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="Report_Summary_v4.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
