import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from datetime import datetime

# --- 1. 定義欄位與關鍵字 ---

# 單一項目：只要找到這行，就直接抓結果
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

# 群組項目：需要掃描多個細項，然後取最大值
# 只要測項名稱包含這些字根，就會被歸類進來
GROUP_KEYWORDS = {
    "PBB": [
        "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", "bromobiphenyl", "溴聯苯", "PBB"
    ],
    "PBDE": [
        "Monobromodiphenyl ether", "Dibromodiphenyl ether", "Tribromodiphenyl ether",
        "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", "Hexabromodiphenyl ether",
        "Heptabromodiphenyl ether", "Octabromodiphenyl ether", "Nonabromodiphenyl ether",
        "Decabromodiphenyl ether", "bromodiphenyl ether", "溴聯苯醚", "PBDE"
    ],
    "PFAS": [
        "PFHxA", "PFHxS", "PFOA", "PFNA", "PFDA", "PFUnDA", "PFDoDA", "PFTrDA", "PFTeDA",
        "FTOH", "FTA", "FTMAC", "FTS", "FTCA", "PFAS", "Perfluoro", "全氟", "Fluorotelomer"
    ]
}

# 最終輸出的欄位順序 (PFAS 加在 PFOS 旁邊)
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
        r"(?:Date|日期|Issue\s*Date).*?([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})",
        r"(?:Date|日期|Issue\s*Date).*?([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                if "-" in match.group(1) and len(match.groups()) == 1:
                    return datetime.strptime(match.group(1), "%d-%b-%Y")
                elif len(match.groups()) == 3:
                    return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except: continue
    return None

def parse_value_priority(value_str):
    """
    決定數值優先級
    Score 3: 數值 (取最大)
    Score 2: Negative
    Score 1: n.d.
    Score 0: 無效/標題
    """
    val = clean_text(value_str).replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    # 排除 PDF 表格標題誤判
    if val_lower in ["result", "limit", "mdl", "loq", "unit", "method", "004", "no.1"]: return (0, 0, "")

    if "n.d." in val_lower or "nd" == val_lower or "<" in val_lower: return (1, 0, "n.d.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "Negative")
    
    # 嘗試抓取數字
    num_match = re.search(r"([\d\.]+)", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            return (3, number, val)
        except: pass
            
    return (0, 0, val)

# --- 3. 核心：動態欄位識別 ---

def identify_columns(header_row):
    item_idx = -1
    result_idx = -1
    
    for i, cell in enumerate(header_row):
        txt = clean_text(cell).lower()
        if "test item" in txt or "tested item" in txt or "測試項目" in txt: item_idx = i
        if "result" in txt or "結果" in txt: result_idx = i
            
    return item_idx, result_idx

def process_files(files):
    # 資料池結構
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        current_date = None
        
        # 暫存該檔案內的群組數據 (PBB, PBDE, PFAS)
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}

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
                        
                        header_row = table[0]
                        item_idx, result_idx = identify_columns(header_row)
                        
                        for row_idx, row in enumerate(table):
                            if row_idx == 0: continue
                            clean_row = [clean_text(cell) for cell in row]
                            if not any(clean_row): continue
                            
                            # 找測項名稱
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            # 跳過標題列
                            if "test item" in item_name.lower() or "測試項目" in item_name: continue

                            # 找結果
                            result = ""
                            # A. 優先用表頭定位
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            # B. 備援：倒著找回來 (找 n.d. 或 數字 或 Negative)
                            if not result:
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    # 排除空值和顯然不是結果的字
                                    if not cell: continue
                                    if "n.d." in c_lower or "negative" in c_lower or re.search(r"^\d+(\.\d+)?$", cell):
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue # 略過無效值

                            # --- A. 匹配單一項目 (Simple) ---
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    # 精確匹配，避免 PFOS 抓到 PFOS-related
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower():
                                            continue 
                                        
                                        data_pool[target_key].append({
                                            "priority": priority,
                                            "filename": filename
                                        })
                                        break

                            # --- B. 匹配群組項目 (PBB, PBDE, PFAS) ---
                            for group_key, keywords in GROUP_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        # 1. 排除 "Sum of" 行 (我們自己算)
                                        if "sum of" in item_name.lower() or "總和" in item_name:
                                            continue
                                        
                                        # 2. 排除 PFOS 本身 (因為它有獨立欄位，不放入 PFAS 群組)
                                        if group_key == "PFAS" and "pfos" in item_name.lower() and "related" not in item_name.lower():
                                            continue

                                        # 收集該檔案內的細項
                                        file_group_data[group_key].append(priority)
                                        break
            
            # --- 檔案掃描結束：結算該檔案的 Group 最大值 ---
            # 這裡會把這份報告裡找到的所有 PBB/PFAS 細項做比較
            for group_key, values in file_group_data.items():
                if values:
                    # 排序規則：數值(3) > Negative(2) > n.d.(1)
                    # 如果都是數值，取最大值
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    
                    # 將這個「代表值」存入總池
                    data_pool[group_key].append({
                        "priority": best_in_file,
                        "filename": filename
                    })

        except Exception as e:
            st.warning(f"檔案 {filename} 解析異常: {e}")

        progress_bar.progress((i + 1) / len(files))

    # --- 4. 聚合 (跨檔案比對) ---
    final_row = {}
    max_val_filename = "" 
    global_max_score = -1

    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
            
        # 跨檔案排序，取最好的結果
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
    
    # 決定顯示哪份檔案名稱
    if global_max_score == 3: 
        final_row["檔案名稱"] = max_val_filename
    else:
        final_row["檔案名稱"] = latest_file_name_by_date if latest_file_name_by_date else (files[0].name if files else "")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v7.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v7.0)")
st.info("💡 v7.0 更新：加入 PFAS 欄位，並支援 PBB/PBDE/PFAS 細項掃描自動取最大值。")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 重新執行"): st.rerun()

    try:
        result_data = process_files(uploaded_files)
        df = pd.DataFrame(result_data)
        
        # 確保欄位順序正確
        for col in OUTPUT_COLUMNS:
            if col not in df.columns: df[col] = ""
        df = df[OUTPUT_COLUMNS]

        st.success("✅ 處理完成！")
        st.dataframe(df)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Summary')
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v7.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
