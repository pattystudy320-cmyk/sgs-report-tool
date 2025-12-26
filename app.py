import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 定義欄位與關鍵字對照表 ---
KEYWORD_MAP = {
    "Pb": ["Lead", "鉛", "Pb"],
    "Cd": ["Cadmium", "鎘", "Cd"],
    "Hg": ["Mercury", "汞", "Hg"],
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)"],
    # PBB/PBDE 關鍵字放寬，確保能抓到 "Sum of PBBs"
    "PBB": ["Sum of PBBs", "多溴聯苯總和", "PBBs"],
    "PBDE": ["Sum of PBDEs", "多溴聯苯醚總和", "PBDEs"],
    "DEHP": ["DEHP", "Di(2-ethylhexyl) phthalate"],
    "BBP": ["BBP", "Butyl benzyl phthalate"],
    "DBP": ["DBP", "Dibutyl phthalate"],
    "DIBP": ["DIBP", "Diisobutyl phthalate"],
    "PFOS": ["PFOS", "Perfluorooctane sulfonates"],
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

def extract_date_from_text(text):
    """
    全方位日期抓取：支援 Jan-01-2025, 2025/01/01, 2025.01.01
    """
    text = text.replace('\n', ' ') # 移除換行以免干擾 regex
    
    # 模式 1: 06-Jan-2025 (SGS 常用)
    match1 = re.search(r"([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})", text)
    if match1:
        try:
            return datetime.strptime(match1.group(1), "%d-%b-%Y")
        except: pass

    # 模式 2: 2025/01/06 or 2025.01.06 (台灣常用)
    match2 = re.search(r"([0-9]{4})[/\.]([0-9]{1,2})[/\.]([0-9]{1,2})", text)
    if match2:
        try:
            # 嘗試建立日期物件
            return datetime(int(match2.group(1)), int(match2.group(2)), int(match2.group(3)))
        except: pass
        
    return None

def parse_value_priority(value_str):
    """決定數值優先級 & 清洗單位"""
    val = str(value_str).replace("mg/kg", "").replace("ppm", "").replace("%", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    if "n.d." in val_lower or "nd" == val_lower: return (1, 0, "n.d.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "Negative")
    
    # 抓取數字 (處理 <5, >100 等符號)
    num_match = re.search(r"([\d\.]+)", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, val)
        except: pass
            
    return (0, 0, val)

def smart_find_result(row):
    """
    ★核心升級：智慧尋找結果與單位欄位
    回傳: (Result_Value, Unit_Text)
    """
    unit_idx = -1
    
    # 1. 先找單位在哪一格 (定位點)
    for i, cell in enumerate(row):
        cell_text = str(cell).lower()
        if "mg/kg" in cell_text or "ppm" in cell_text or "%" in cell_text:
            unit_idx = i
            break
    
    found_unit = row[unit_idx] if unit_idx != -1 else ""
    found_result = ""

    # 2. 根據單位位置推算結果
    if unit_idx != -1:
        # 根據 SGS 慣例：單位(Unit) -> MDL -> 結果(Result)
        # 所以結果通常在 單位 + 2
        result_idx = unit_idx + 2
        if result_idx < len(row):
            found_result = row[result_idx]
        else:
            # 如果爆出範圍，試試看 +1 (有時候沒有 MDL 欄位)
            if unit_idx + 1 < len(row):
                found_result = row[unit_idx + 1]
    else:
        # 3. 如果找不到單位 (例如 PBB Sum 欄位可能沒寫單位)，改找關鍵字 "n.d."
        for cell in row:
            txt = str(cell).strip()
            if "n.d." in txt.lower() or "negative" in txt.lower():
                found_result = txt
                break
                
    return found_result, found_unit

# --- 3. 主流程 ---

def process_files(files):
    data_pool = {key: [] for key in KEYWORD_MAP.keys()}
    all_dates = []
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        current_date = None
        
        try:
            with pdfplumber.open(file) as pdf:
                # 抓日期
                first_page_text = pdf.pages[0].extract_text()
                current_date = extract_date_from_text(first_page_text)
                if current_date:
                    all_dates.append((current_date, filename))

                # 抓表格
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            clean_row = [str(cell).replace('\n', ' ').strip() if cell else "" for cell in row]
                            
                            # 基本過濾
                            if len(clean_row) >= 3:
                                item_name = clean_row[0]
                                
                                # 跳過標題列
                                if "測試項目" in item_name or "Test Items" in item_name:
                                    continue

                                for target_key, keywords in KEYWORD_MAP.items():
                                    for kw in keywords:
                                        if kw in item_name:
                                            # ★ 使用智慧定位找結果
                                            result, unit = smart_find_result(clean_row)
                                            
                                            # 若 result 為空，可能是沒抓對，保留彈性
                                            if not result and len(clean_row) > 4:
                                                # 最後一搏：有些格式 Result 在最後一格 (index -1) 或是 倒數第二格 (index -2)
                                                # 如果 clean_row[4] 看起來像結果...
                                                pass 

                                            priority = parse_value_priority(result)
                                            
                                            # 存入資料
                                            data_pool[target_key].append({
                                                "priority": priority,
                                                "filename": filename,
                                                "date": current_date,
                                                "unit": unit
                                            })
                                            break 
        except Exception as e:
            st.warning(f"檔案 {filename} 讀取部分失敗: {e}")

        progress_bar.progress((i + 1) / len(files))

    # --- 聚合 ---
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

    # 日期處理
    final_date_str = ""
    latest_file_name_by_date = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d") # 統一轉為 2025/01/01 格式
        latest_file_name_by_date = latest_date_record[1]
    
    final_row["單位"] = default_unit if default_unit else "mg/kg"
    final_row["日期"] = final_date_str
    
    if global_max_score == 3: 
        final_row["檔案名稱"] = max_val_filename
    else:
        final_row["檔案名稱"] = latest_file_name_by_date if latest_file_name_by_date else (files[0].name if files else "")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v3.0", layout="wide")
st.title("📄 SGS 檢測報告批次聚合工具 (智慧修正版)")
st.info("💡 此版本已修復：單位錯置、日期抓取、數值與MDL混淆的問題。")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 重新執行分析"):
        st.rerun()

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v3.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"發生錯誤: {e}")
