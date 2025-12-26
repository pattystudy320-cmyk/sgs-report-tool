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
    修正後的日期抓取：
    1. 限制年份必須是 20xx (避免抓到 IEC 62321)
    2. 支援常見格式
    """
    text = clean_text(text)
    
    # Regex 針對年份做限制 (20\d{2}) -> 2000~2099
    patterns = [
        # 格式: 2023/03/03, 2023-03-03, 2023.03.03
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])",
        # 格式: 03-Mar-2023, 03-Jan-2025
        r"(0?[1-9]|[12][0-9]|3[01])-[a-zA-Z]{3}-(20\d{2})",
        # 格式: Mar 03, 2023, Oct 08, 2024
        r"([a-zA-Z]{3})\s+(0?[1-9]|[12][0-9]|3[01])[,]\s+(20\d{2})"
    ]
    
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                groups = match.groups()
                full_match = match.group(0)
                
                # 嘗試解析各種格式
                try:
                    dt = datetime.strptime(full_match.replace(".", "/").replace("-", "/"), "%Y/%m/%d")
                except:
                    try:
                        dt = datetime.strptime(full_match, "%d-%b-%Y")
                    except:
                        try:
                            # 處理 "Oct 08, 2024" 這種格式
                            # 移除逗號以便解析
                            clean_date = full_match.replace(",", "")
                            dt = datetime.strptime(clean_date, "%b %d %Y")
                        except:
                            pass
                
                # ★ 關鍵：年份過濾器 (排除 IEC 62321) ★
                if dt and 2000 <= dt.year <= 2030: 
                    found_dates.append(dt)
            except: continue
            
    if found_dates:
        return max(found_dates) # 回傳最新日期
    return None

def parse_value_priority(value_str):
    raw_val = clean_text(value_str)
    
    # 處理特殊格式: 0.01 (100) -> 切除括號後面的限值
    if "(" in raw_val:
        raw_val = raw_val.split("(")[0].strip()
        
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    # 排除清單 (黑名單)
    if val_lower in ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "002", "no.1", "---", "-", "limits", "mg/kg", "ppm"]: 
        return (0, 0, "")

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

# --- 3. 核心：智慧欄位識別 (黑名單機制) ---

def check_pfas_trigger(full_text):
    for phrase in PFAS_TRIGGER_PHRASES:
        if phrase.lower() in full_text.lower():
            return True
    return False

def identify_columns(header_row):
    """
    回傳: item_idx, result_idx, is_limit_table(布林值)
    """
    item_idx = -1
    result_idx = -1
    is_limit_table = False
    
    # 轉小寫方便比對
    header_text_all = " ".join([str(c).lower() for c in header_row])
    
    # ★ 關鍵修正：偵測這是「限值表」嗎？
    # 如果標題包含 "restricted substances" 或 "limits" 且完全沒有 "result" 或 數字編號
    # Intertek/CTI 的限值表通常長這樣
    if ("restricted substances" in header_text_all or "limits" in header_text_all or "rohs limit" in header_text_all) and \
       not any(x in header_text_all for x in ["result", "結果", "001", "002", "003", "004", "no.1"]):
        return -1, -1, True # 標記為限值表，稍後跳過

    for i, cell in enumerate(header_row):
        txt = clean_text(cell).lower()
        if "test item" in txt or "tested item" in txt or "測試項目" in txt: item_idx = i
        
        # 支援多種結果欄位寫法: Result, 結果, 001~009, No.1, Green material
        if "result" in txt or "結果" in txt or re.search(r"00[1-9]", txt) or "no.1" in txt or "green material" in txt: 
            result_idx = i
            
    return item_idx, result_idx, False

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    
    pb_tracker = {
        "max_score": -1, 
        "max_value": -1.0,
        "filenames": []
    }
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        full_text_content = ""

        try:
            with pdfplumber.open(file) as pdf:
                # 1. 抓日期 (掃描前3頁)
                file_dates = []
                for p_idx in range(min(3, len(pdf.pages))):
                    page_txt = pdf.pages[p_idx].extract_text()
                    if page_txt:
                        full_text_content += page_txt
                        d = extract_date_from_text(page_txt)
                        if d: file_dates.append(d)
                
                # 抓取該檔案中最新的日期
                if file_dates:
                    all_dates.append((max(file_dates), filename))
                
                # 補讀文字
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
                        item_idx, result_idx, is_limit_table = identify_columns(header_row)
                        
                        # ★ 如果是限值表，直接跳過這張表 ★
                        if is_limit_table:
                            continue

                        # 表頭記憶 (處理跨頁表格)
                        if result_idx != -1:
                            last_result_idx = result_idx
                            last_item_idx = item_idx if item_idx != -1 else 0
                        else:
                            if last_result_idx != -1:
                                result_idx = last_result_idx
                                item_idx = last_item_idx
                        
                        for row_idx, row in enumerate(table):
                            clean_row = [clean_text(cell) for cell in row]
                            row_txt = "".join(clean_row).lower()
                            # 跳過標題行
                            if "test item" in row_txt or "result" in row_txt or "restricted substances" in row_txt: continue
                            if not any(clean_row): continue
                            
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            result = ""
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            if not result:
                                # 備援掃描 (倒著找，但避開明顯是限值的數字)
                                for cell in reversed(clean_row):
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    # 排除 Limit 1000, 100, 5, 2 (MDL)
                                    # 這裡做一個簡單過濾：如果是純整數且是 1000, 100, 50 這種常見限值，且這一列前面還有其他數字，則可能是限值
                                    # 為求保險，優先找 nd 或 negative
                                    if "nd" in c_lower or "n.d." in c_lower or "negative" in c_lower:
                                        result = cell
                                        break
                                    # 如果是數字
                                    if re.search(r"^\d+(\.\d+)?$", cell):
                                        # 簡單判斷：如果這個數字是 1000, 100，很可能是限值，先不抓，除非沒別的選擇
                                        if float(cell) in [1000, 100, 50, 25, 10, 5, 2]:
                                            continue
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            # A. Simple 項目 (含 Pb 追蹤)
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        if target_key == "PFOS" and "related" in item_name.lower(): continue 
                                        
                                        data_pool[target_key].append({
                                            "priority": priority,
                                            "filename": filename
                                        })
                                        
                                        # Pb 追蹤
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

                            # B. Group 項目
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
st.set_page_config(page_title="SGS 報告聚合工具 v15.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v15.0)")
st.info("💡 v15.0：修正日期誤判、自動過濾 RoHS Limit 限值表、支援 CTI/Intertek 多種格式。")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v15.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
