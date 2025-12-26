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
    text = clean_text(text)
    patterns = [
        r"(?:Date|日期|Issue).*?([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})",
        r"(?:Date|日期|Issue).*?([0-9]{2}-[a-zA-Z]{3}-[0-9]{4})",
        r"([0-9]{4})[/\.-]([0-9]{1,2})[/\.-]([0-9]{1,2})"
    ]
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                groups = match.groups()
                if len(groups) == 3:
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                elif len(groups) == 1:
                    return datetime.strptime(groups[0], "%d-%b-%Y")
            except: continue
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
    if val_lower in ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "no.1", "---", "-"]: 
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
    回傳:
    1. item_idx (測項)
    2. result_idx (結果)
    3. exclude_indices (黑名單：絕對不能讀的欄位，如 Limit, MDL, Unit)
    """
    item_idx = -1
    result_idx = -1
    exclude_indices = set()
    
    for i, cell in enumerate(header_row):
        txt = clean_text(cell).lower()
        
        # 1. 標記測項
        if "test item" in txt or "tested item" in txt or "測試項目" in txt:
            item_idx = i
            continue
            
        # 2. 標記黑名單 (Limit, MDL, Unit, Method)
        # 這些欄位絕對不是結果，標記起來，之後掃描時跳過
        if any(x in txt for x in ["limit", "mdl", "loq", "rl", "unit", "method", "限值", "單位", "方法"]):
            exclude_indices.add(i)
            continue
        
        # 3. 標記結果
        # 只有當它包含 Result 關鍵字，且沒有被上面的黑名單攔截時
        if "result" in txt or "結果" in txt or "001" in txt or "004" in txt or "no.1" in txt: 
            result_idx = i
            
    return item_idx, result_idx, exclude_indices

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
                # 抓日期
                date_found = None
                for p_idx in range(min(3, len(pdf.pages))):
                    page_txt = pdf.pages[p_idx].extract_text()
                    if page_txt:
                        full_text_content += page_txt
                        if not date_found:
                            d = extract_date_from_text(page_txt)
                            if d: date_found = d
                if date_found: all_dates.append((date_found, filename))
                
                # 補讀文字
                for p in pdf.pages[3:]:
                    full_text_content += (p.extract_text() or "")
                pfas_active = check_pfas_trigger(full_text_content)

                # 抓表格
                last_result_idx = -1 
                last_item_idx = 0
                last_exclude_indices = set()

                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        header_row = table[0]
                        item_idx, result_idx, exclude_indices = identify_columns(header_row)
                        
                        # 表頭記憶邏輯
                        if result_idx != -1:
                            last_result_idx = result_idx
                            last_item_idx = item_idx if item_idx != -1 else 0
                            last_exclude_indices = exclude_indices
                        else:
                            # 沿用上一個表格的設定 (針對 PBB 跨頁)
                            if last_result_idx != -1:
                                result_idx = last_result_idx
                                item_idx = last_item_idx
                                exclude_indices = last_exclude_indices
                        
                        for row_idx, row in enumerate(table):
                            clean_row = [clean_text(cell) for cell in row]
                            row_txt = "".join(clean_row).lower()
                            if "test item" in row_txt or "result" in row_txt: continue
                            if not any(clean_row): continue
                            
                            target_item_col = item_idx if item_idx != -1 else 0
                            if target_item_col >= len(clean_row): continue
                            item_name = clean_row[target_item_col]
                            
                            result = ""
                            # 策略 A: 明確知道結果在哪一欄
                            if result_idx != -1 and result_idx < len(clean_row):
                                result = clean_row[result_idx]
                            
                            # 策略 B: 備援掃描 (最危險的步驟，加上防護)
                            if not result:
                                # 倒著找，但要避開黑名單 (Limit, MDL)
                                for col_i in range(len(clean_row)-1, -1, -1):
                                    # ★ 關鍵修正：如果這一欄是 Limit/MDL，跳過！
                                    if col_i in exclude_indices:
                                        continue
                                    
                                    cell = clean_row[col_i]
                                    c_lower = cell.lower()
                                    if not cell: continue
                                    
                                    # 找 n.d. 或 數字
                                    if "nd" in c_lower or "n.d." in c_lower or "negative" in c_lower or re.search(r"^\d+(\.\d+)?", cell):
                                        result = cell
                                        break
                            
                            priority = parse_value_priority(result)
                            if priority[0] == 0: continue 

                            # Simple 項目
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

                            # Group 項目
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
st.set_page_config(page_title="SGS 報告聚合工具 v14.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v14.0)")
st.info("💡 v14.0 重大更新：加入黑名單機制，防止誤抓 Limit/MDL 欄位。")

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
        
        st.download_button("📥 下載 Excel", data=output.getvalue(), file_name="SGS_Summary_v14.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        st.error(f"系統錯誤: {e}")
