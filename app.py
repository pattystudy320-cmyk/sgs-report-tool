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
    "PBB": ["Sum of PBBs", "多溴聯苯總和"],
    "PBDE": ["Sum of PBDEs", "多溴聯苯醚總和"],
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
    修正版：增強對 SGS 日期格式的相容性
    能抓取: "Date: ...", "日期: ...", "日期(Date): ..."
    """
    # Regex 解釋: (?:Date|日期) = 找這兩個字開頭, .*? = 中間可有任意雜字, :\s* = 冒號與空白
    match = re.search(r"(?:Date|日期).*?:\s*([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})", text, re.IGNORECASE)
    if match:
        try:
            date_str = match.group(1)
            return datetime.strptime(date_str, "%d-%b-%Y")
        except:
            return None
    return None

def parse_value_priority(value_str):
    """
    決定數值的優先順序
    修正版：強制移除單位字串
    """
    # 1. 轉字串並強制移除常見單位
    val = str(value_str).replace("mg/kg", "").replace("ppm", "").strip()
    
    if not val:
        return (0, 0, "")
    
    val_lower = val.lower()

    # 2. 邏輯判斷
    if "n.d." in val_lower or "nd" == val_lower:
        return (1, 0, "n.d.")
    
    if "negative" in val_lower or "陰性" in val_lower:
        return (2, 0, "Negative")
    
    # 3. 嘗試抓取純數字 (移除 < 或 > 符號以便比大小)
    # 例如 "<5" 我們當作 0 處理，但如果有 "100" 則保留
    num_match = re.search(r"([\d\.]+)", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, val) # 回傳清洗後的 val (不含單位)
        except:
            pass
            
    return (0, 0, val)

# --- 3. 核心解析邏輯 ---

def process_files(files):
    data_pool = {key: [] for key in KEYWORD_MAP.keys()}
    all_dates = []
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        current_date = None
        
        try:
            with pdfplumber.open(file) as pdf:
                # 1. 抓取日期
                first_page_text = pdf.pages[0].extract_text()
                current_date = extract_date_from_text(first_page_text)
                if current_date:
                    all_dates.append((current_date, filename))

                # 2. 抓取表格
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            clean_row = [str(cell).replace('\n', ' ').strip() if cell else "" for cell in row]
                            
                            if len(clean_row) >= 5 and "測試項目" not in clean_row[0]:
                                item_name = clean_row[0]
                                unit = clean_row[2]
                                result = clean_row[4] # 預設抓第5欄
                                
                                # ★修正：針對 DEHP 類可能抓錯欄位或是單位黏在數值上的處理
                                # 如果發現 result 欄位是空的，但後面欄位有值，嘗試往後抓
                                if not result and len(clean_row) > 5:
                                     # 有時候格子歪掉，試著抓下一格，但需小心不要抓到限值
                                     # 這裡我們先相信上面的單位清洗功能
                                     pass

                                for target_key, keywords in KEYWORD_MAP.items():
                                    for kw in keywords:
                                        if kw in item_name:
                                            # 如果是 DEHP 系列，且抓到的值大於 100 (通常結果不會剛好是整數限值)，
                                            # 可能是抓到限值了。這裡做一個簡單防呆：
                                            # 如果 result 看起來像限值 (如 "1000") 且 row[3] (MDL) 存在，
                                            # 有可能 n.d. 寫在 index 3 或 index 5? 
                                            # (暫不加入過度複雜邏輯，先靠 remove unit 解決顯示問題)
                                            
                                            priority = parse_value_priority(result)
                                            data_pool[target_key].append({
                                                "priority": priority,
                                                "filename": filename,
                                                "date": current_date,
                                                "unit": unit
                                            })
                                            break 
        except Exception as e:
            st.warning(f"檔案 {filename} 讀取時發生微小錯誤，已略過部分內容: {e}")

        progress_bar.progress((i + 1) / len(files))

    # --- 4. 數據聚合 ---
    final_row = {}
    max_val_filename = "" 
    global_max_score = -1
    default_unit = ""

    # 找出各項目的最佳值
    for key in KEYWORD_MAP.keys():
        candidates = data_pool[key]
        if not candidates:
            final_row[key] = "" 
            continue
            
        # 排序：優先級(3>2>1) -> 數值大小 -> 
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2] # 填入清洗後的文字
        
        # 抓單位 (優先抓有數值的)
        if best_record['priority'][0] == 3 and not default_unit:
            default_unit = best_record['unit']
            
        # 決定最大值檔案
        if best_record['priority'][0] > global_max_score:
            global_max_score = best_record['priority'][0]
            max_val_filename = best_record['filename']
        elif best_record['priority'][0] == 3 and global_max_score == 3:
             # 若同為數值，這裡簡單更新為當前檔案
             max_val_filename = best_record['filename']

    # 決定日期
    final_date_str = ""
    latest_file_name_by_date = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%d-%b-%Y")
        latest_file_name_by_date = latest_date_record[1]
    
    final_row["單位"] = default_unit if default_unit else "mg/kg"
    final_row["日期"] = final_date_str
    
    # 決定檔案名稱 (數值最大者優先，否則取日期最新者)
    if global_max_score == 3: 
        final_row["檔案名稱"] = max_val_filename
    else:
        # 如果全都是 n.d. 或 Negative，顯示最新日期的那個檔名
        final_row["檔案名稱"] = latest_file_name_by_date if latest_file_name_by_date else (files[0].name if files else "")

    return [final_row]

# --- 5. Streamlit 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具", layout="wide")

st.title("📄 SGS 檢測報告批次聚合工具")
st.info("💡 提示：若要上傳多份檔案，請在選擇視窗中按住 Ctrl 或 Shift 鍵一次選取所有檔案。")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    # 重新整理按鈕 (解決有時需要重跑的需求)
    if st.button("🔄 重新執行分析"):
        st.rerun()

    try:
        result_data = process_files(uploaded_files)
        df = pd.DataFrame(result_data)
        
        # 補齊空欄位並排序
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[OUTPUT_COLUMNS]

        st.success("✅ 處理完成！")
        st.dataframe(df)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button(
            label="📥 下載 Excel",
            data=output.getvalue(),
            file_name="SGS_Summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except Exception as e:

        st.error(f"發生錯誤: {e}")
