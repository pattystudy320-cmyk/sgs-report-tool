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
        "Polybrominated Biphenyls (PBBs)", # 您指定的關鍵字
        "Sum of PBBs", "多溴聯苯總和",
        "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", 
        "Decabromobiphenyl", "bromobiphenyl"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers (PBDEs)", # 您指定的關鍵字
        "Sum of PBDEs", "多溴聯苯醚總和",
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
    text = clean_text(text)
    # 強力日期匹配：包含 Date: 2025/01/01 或 Issue Date: 06-Jan-2025
    patterns = [
        r"(?:Date|日期|Issue).*?([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})", # 2025/01/06
        r"(?:Date|日期|Issue).*?([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})", # 06-Jan-2025
        r"([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})" # 純日期格式 (備援)
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                groups = match.groups()
                if len(groups) == 3: # YYYY/MM/DD
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                elif len(groups) == 1: # DD-Mon-YYYY
                    return datetime.strptime(groups[0], "%d-%b-%Y")
            except: continue
    return None

def parse_value_priority(value_str):
    """
    數值解析邏輯：
    回傳 (分數, 數值, 顯示文字)
    3: 有數值 (10.5)
    2: Negative
    1: N.D.
    0: 無效
    """
    # 移除單位與雜訊
    val = clean_text(value_str).replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    # 排除不是結果的字
    if val_lower in ["result", "limit", "mdl", "loq", "unit", "method", "004", "no.1", "---", "-"]: 
        return (0, 0, "")

    if "n.d." in val_lower or "nd" == val_lower or "<" in val_lower: 
        return (1, 0, "n.d.")
    if "negative" in val_lower or "陰性" in val_lower: 
        return (2, 0, "Negative")
    
    # 抓取純數字 (包含小數點)
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
    """
    智慧判斷 Result 在哪一欄
    """
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
    
    # Pb 最大值追蹤器
    pb_tracker = {
        "max_score": -1, # 0=無, 1=nd, 2=neg, 3=num
        "max_value": -1.0,
        "filename": ""
    }
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        full_text_content = ""

        try:
            with pdfplumber.open(file) as pdf:
                # 1. 抓日期 (掃描前三頁，範圍擴大)
                date_found = None
                for p_idx in range(min(3, len(pdf.pages))):
                    page_txt = pdf.pages[p_idx].extract_text()
                    if page_txt:
                        full_text_content += page_txt
                        if not date_found:
                            d = extract_date_from_text(page_txt)
                            if d: date_found = d
                            
                if date_found:
                    all_dates.append((date_found, filename))
                
                # 補讀剩餘頁面
                for p in pdf.pages[3:]:
                    full_text_content += (p.extract_text() or "")

                pfas_active = check_pfas_trigger(full_text_content)

                # 2. 抓表格
                last_result_idx = -1 
                last_item_idx = 0

                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        header_row = table[0]
                        item_idx, result_idx = identify_columns(header_row)
                        
                        # 表頭記憶：如果當前表格沒表頭，沿用上一個
                        if result_idx != -1:
                            last_result_idx = result_idx
                            last_item_idx = item_idx if item_idx != -1 else 0
                        else:
                            if last_result_idx != -1:
                                result_idx = last_result_idx
                                item_idx = last_item_idx
                        
                        for row_idx, row in enumerate(table):
                            clean_row = [clean_text(cell) for cell in row]
                            # 跳過顯然是表頭的行
                            row_text_joined = "".join(clean_row).lower()
                            if "test item" in row_text_joined or "result" in row_text_joined: continue
                            if not any(clean_row): continue
                            
                            # 找測項
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            # 找結果
                            result = ""
                            # 優先：依欄位索引
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            # 備援：特徵搜尋 (找 nd 或 數字)
                            # 針對 "Tin Layer" 這種格式，有時候 Result 在 Unit 的後面
                            if not result:
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    if "n.d." in c_lower or "negative" in c_lower or re.search(r"^\d+(\.\d+)?$", cell):
                                        # 簡單過濾：如果這格長得像 MDL (整數 2, 5, 10)，且前面還有一格也是數字，可能抓錯
                                        # 但這裡先相信它
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            # --- A. Simple (Pb/Cd...) ---
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower(): continue 
                                        
                                        data_pool[target_key].append({
                                            "priority": priority,
                                            "filename": filename
                                        })
                                        
                                        # ★ Pb 最大值檔案追蹤 ★
                                        if target_key == "Pb":
                                            # 邏輯：有數值(3) > Negative(2) > n.d.(1)
                                            # 如果找到更大的分數，或者同分但數值更大，就更新
                                            if priority[0] > pb_tracker["max_score"]:
                                                pb_tracker["max_score"] = priority[0]
                                                pb_tracker["max_value"] = priority[1]
                                                pb_tracker["filename"] = filename
                                            elif priority[0] == 3 and priority[1] > pb_tracker["max_value"]:
                                                pb_tracker["max_value"] = priority[1]
                                                pb_tracker["filename"] = filename
                                        break

                            # --- B. Group (PBB/PBDE/PFAS) ---
                            for group_key, keywords in GROUP_KEYWORDS.items():
                                if group_key == "PFAS" and not pfas_active: continue

                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if group_key == "PFAS" and "pfos" in item_name.lower() and "related" not in item_name.lower():
                                            continue
                                        
                                        # 不管是不是 "Sum of"，只要抓到就納入計算
                                        file_group_data[group_key].append(priority)
                                        break
            
            # --- 檔案結算 (PBB/PBDE/PFAS) ---
            for group_key, values in file_group_data.items():
                if values:
                    # 邏輯：一份報告中，只要有一個細項是數值，就取最大值。全都是 n.d. 才是 n.d.
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
            
        # 取所有報告中最大的那個值
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2]

    # 日期處理 (取最新)
    final_date_str = ""
    latest_file = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
        latest_file = latest_date_record[1] # 備用：日期最新的檔案
    
    final_row["日期"] = final_date_str
    
    # ★ 檔案名稱邏輯：顯示 Pb 值最大的檔案 ★
    if pb_tracker["filename"]:
        final_row["檔案名稱"] = pb_tracker["filename"]
    else:
        # 如果 Pb 全都沒抓到，改顯示日期最新的檔案 (防呆)
        final_row["檔案名稱"] = latest_file if latest_file else (files[0].name if files else "")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS 報告聚合工具 v11.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v11.0)")
st.info("💡 更新：修正 Pb 最大值檔案追蹤、日期格式支援、PBB/PBDE 群組關鍵字。")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v11.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
