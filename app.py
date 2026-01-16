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
    "Cr6+": ["Hexavalent Chromium", "六價鉻", "Cr(VI)", "Chromium VI", "Cr6+"],
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
        "Polybromobiphenyl", "Monobromobiphenyl", "Dibromobiphenyl", "Tribromobiphenyl", 
        "Tetrabromobiphenyl", "Pentabromobiphenyl", "Hexabromobiphenyl", 
        "Heptabromobiphenyl", "Octabromobiphenyl", "Nonabromobiphenyl", "Decabromobiphenyl"
    ],
    "PBDE": [
        "Polybrominated Diphenyl Ethers", "PBDEs", "Sum of PBDEs", "多溴聯苯醚總和",
        "Polybromodiphenyl ether", "Monobromodiphenyl ether", "Dibromodiphenyl ether", 
        "Tribromodiphenyl ether", "Tetrabromodiphenyl ether", "Pentabromodiphenyl ether", 
        "Hexabromodiphenyl ether", "Heptabromodiphenyl ether", "Octabromodiphenyl ether", 
        "Nonabromodiphenyl ether", "Decabromodiphenyl ether"
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
    # v35.0: 增強日期格式，支援 05-Jan-2023 類型的格式
    patterns = [
        r"(20\d{2})[/\.-](0?[1-9]|1[0-2])[/\.-](0?[1-9]|[12][0-9]|3[01])", 
        r"(0?[1-9]|[12][0-9]|3[01])\s*[-/]\s*([a-zA-Z]{3})\s*[-/]\s*(20\d{2})", 
        r"([a-zA-Z]{3})\.?\s+(0?[1-9]|[12][0-9]|3[01])[,\s]+\s*(20\d{2})",
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(0?[1-9]|[12][0-9]|3[01])\s*,?\s*(20\d{2})"
    ]
    found_dates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                dt = None
                full_match = match.group(0)
                clean_str = re.sub(r"[,./-]", " ", full_match) # 統一分隔符
                clean_str = " ".join(clean_str.split())
                
                for fmt in ["%Y %m %d", "%d %b %Y", "%b %d %Y", "%B %d %Y"]:
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
        # 常見法規限值排除
        if n in [1000.0, 100.0, 50.0, 5.0, 2.0]: return True
        return False
    except: return False

def parse_value_priority(value_str):
    raw_val = clean_text(value_str)
    
    # 移除括號內的內容 (如 MDL 說明)
    if "(" in raw_val:
        raw_val = raw_val.split("(")[0].strip()
    
    val = raw_val.replace("mg/kg", "").replace("ppm", "").replace("%", "").replace("µg/cm²", "").strip()
    
    if not val: return (0, 0, "")
    val_lower = val.lower()

    ignore_list = ["result", "limit", "mdl", "loq", "rl", "unit", "method", "004", "001", "no.1", "---", "-", "limits", "requirement"]
    if val_lower in ignore_list: return (0, 0, "")

    # 排除 CAS No
    if re.search(r"\d+-\d+-\d+", val): return (0, 0, "") 
    
    # ND / Negative 判定
    if "nd" in val_lower or "n.d." in val_lower or "<" in val_lower or "not detected" in val_lower: return (1, 0, "N.D.")
    if "negative" in val_lower or "陰性" in val_lower: return (2, 0, "NEGATIVE")
    
    # 數值判定
    # 先過濾掉看起來像限值的純數字
    num_only_match = re.search(r"^[\d\.]+$", val)
    if num_only_match:
        if is_suspicious_limit_value(val): return (0, 0, "")

    # 提取數值 (包含特殊符號如 186802 ▲)
    # v35.0: 優化 Regex，確保抓到數字開頭的字串
    num_match = re.search(r"^([\d\.]+)\s*(.*)$", val)
    if num_match:
        try:
            number = float(num_match.group(1))
            full_str = val 
            return (3, number, full_str)
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
    if "tuv" in txt: return "TUV"
    return "OTHERS"

# --- 3. 核心：表格識別 (v35.0 強化版) ---

def identify_columns_by_company(table, company):
    item_idx = -1
    result_idx = -1
    
    # 掃描前幾行找出表頭
    max_scan_rows = min(4, len(table)) # 增加掃描行數
    
    # 策略 1: 尋找 "Item" 欄位
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if "test item" in txt or "tested item" in txt or "測試項目" in txt or "substance name" in txt:
                if item_idx == -1: item_idx = c_idx
                
    # 策略 2: 尋找 "Result" 欄位 (根據不同公司邏輯)
    for r in range(max_scan_rows):
        row = table[r]
        for c_idx, cell in enumerate(row):
            txt = clean_text(cell).lower()
            if not txt: continue
            
            # 排除明顯非結果的欄位
            if "limit" in txt or "mdl" in txt or "rl" in txt or "unit" in txt or "method" in txt or "cas" in txt:
                continue

            # SGS 邏輯
            if company == "SGS":
                if "result" in txt or "結果" in txt or re.search(r"\b(no\.|00[1-9])", txt):
                     if result_idx == -1: result_idx = c_idx
            
            # Intertek 邏輯 (Result 常在 MDL 前面)
            elif company == "INTERTEK":
                if "result" in txt or "claimed" in txt:
                     if result_idx == -1: result_idx = c_idx

            # CTI / 其他
            else:
                if "result" in txt or "結果" in txt:
                     if result_idx == -1: result_idx = c_idx

    # 如果還是沒找到 Item 欄位，預設第 0 欄
    if item_idx == -1: item_idx = 0
    
    # 如果還是沒找到 Result 欄位，SGS 特殊處理 (找最後一欄非空的)
    if result_idx == -1 and len(table[0]) > 2:
        # 假設最後一欄是結果，但需小心 Note 欄位
        result_idx = len(table[0]) - 1

    return item_idx, result_idx

# --- 4. 核心：文字模式 ---

def parse_text_lines(text, data_pool, file_group_data, filename):
    lines = text.split('\n')
    
    for line in lines:
        line_clean = clean_text(line)
        if not line_clean: continue
        
        # 尋找關鍵字
        matched_simple = None
        for key, keywords in SIMPLE_KEYWORDS.items():
            for kw in keywords:
                if kw in line_clean and "test item" not in line_clean.lower():
                    matched_simple = key
                    break
            if matched_simple: break
        
        matched_group = None
        if not matched_simple:
            for group_key, keywords in GROUP_KEYWORDS.items():
                for kw in keywords:
                    if kw in line_clean:
                        matched_group = group_key
                        break
                if matched_group: break
        
        if matched_simple or matched_group:
            parts = line_clean.split()
            if len(parts) < 2: continue
            
            found_val = ""
            # 從尾部開始找數值
            for part in reversed(parts):
                p_lower = part.lower()
                # 排除干擾字
                if p_lower in ["mg/kg", "ppm", "uqt", "loq", "mdl", "---", "-"]: continue
                
                # 檢查是否為數值或 N.D.
                priority = parse_value_priority(part)
                if priority[0] > 0:
                    found_val = part
                    break
            
            if found_val:
                priority = parse_value_priority(found_val)
                if matched_simple:
                    data_pool[matched_simple].append({
                        "priority": priority,
                        "filename": filename
                    })
                elif matched_group:
                    file_group_data[matched_group].append(priority)

# --- 主程式 ---

def process_files(files):
    data_pool = {key: [] for key in OUTPUT_COLUMNS if key not in ["日期", "檔案名稱"]}
    all_dates = []
    
    # Pb 全局追蹤器
    global_tracker = {
        "Pb": {"max_score": -1, "max_value": -1.0, "filename": ""}
    }
    
    progress_bar = st.progress(0)
    
    for i, file in enumerate(files):
        filename = file.name
        file_group_data = {key: [] for key in GROUP_KEYWORDS.keys()}
        
        try:
            with pdfplumber.open(file) as pdf:
                file_dates = []
                full_text_content = "" 
                
                # 1. 提取文字與日期
                for p_idx, page in enumerate(pdf.pages):
                    page_txt = page.extract_text() or ""
                    full_text_content += page_txt + "\n"
                    
                    if p_idx < 3: # 只在前3頁找日期
                        d = extract_date_from_text(page_txt)
                        if d: file_dates.append(d)
                
                if file_dates: all_dates.append((max(file_dates), filename))
                company = identify_company(full_text_content[:2000]) # 用前2000字判斷公司
                
                if check_pfas_in_summary(full_text_content[:2000]):
                    data_pool["PFAS"].append({"priority": (4, 0, "REPORT"), "filename": filename})

                # 2. 引擎 A: 表格模式
                has_table_data = False
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table or len(table) < 2: continue
                        
                        item_idx, result_idx = identify_columns_by_company(table, company)
                        if result_idx == -1: continue # 找不到結果欄，跳過此表

                        for row in table:
                            clean_row = [clean_text(cell) for cell in row]
                            # v35.0: 如果 Item 欄是空的 (合併儲存格問題)，嘗試用上一列的 Item (簡單版)
                            # 這裡不做複雜的前向填充，避免誤判，直接跳過無 Item 的列
                            if len(clean_row) <= item_idx or not clean_row[item_idx]: continue
                            
                            item_name = clean_row[item_idx]
                            
                            # 檢查是否為標題行
                            if "test item" in item_name.lower() or "result" in item_name.lower(): continue
                            
                            # 提取結果
                            result_cell = ""
                            if result_idx < len(clean_row):
                                result_cell = clean_row[result_idx]
                            
                            # 若指定欄位無值，嘗試整行搜尋 N.D. (防呆)
                            if not result_cell:
                                for cell in clean_row:
                                    if "n.d." in cell.lower() or "not detected" in cell.lower():
                                        result_cell = cell
                                        break

                            priority = parse_value_priority(result_cell)
                            if priority[0] == 0: continue
                            
                            has_table_data = True

                            # 匹配 SIMPLE KEYWORDS
                            for target_key, keywords in SIMPLE_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        # 排除 PFOS 相關化合物 (以免誤抓 derivatives)
                                        if target_key == "PFOS" and ("related" in item_name.lower() or "derivative" in item_name.lower()): continue
                                        
                                        data_pool[target_key].append({"priority": priority, "filename": filename})
                                        
                                        # 更新 Pb 全局追蹤
                                        if target_key == "Pb":
                                            score, val, _ = priority
                                            if score > global_tracker["Pb"]["max_score"]:
                                                global_tracker["Pb"]["max_score"] = score
                                                global_tracker["Pb"]["max_value"] = val
                                                global_tracker["Pb"]["filename"] = filename
                                            elif score == global_tracker["Pb"]["max_score"] and val > global_tracker["Pb"]["max_value"]:
                                                global_tracker["Pb"]["max_value"] = val
                                                global_tracker["Pb"]["filename"] = filename
                                        break
                            
                            # 匹配 GROUP KEYWORDS
                            for group_key, keywords in GROUP_KEYWORDS.items():
                                for kw in keywords:
                                    if kw.lower() in item_name.lower():
                                        file_group_data[group_key].append(priority)
                                        break
                
                # 3. 引擎 B: 文字模式 (如果該檔案在表格沒抓到 Pb，或是 SGS 舊版報告)
                # v35.0: 強制對 SGS 使用文字備援，或表格沒抓到資料時使用
                pb_found_in_file = any(d['filename'] == filename for d in data_pool["Pb"])
                if not pb_found_in_file or (company == "SGS" and not has_table_data):
                    parse_text_lines(full_text_content, data_pool, file_group_data, filename)
                    
                    # 重新檢查文字模式抓到的 Pb 以更新 Tracker
                    for d in data_pool["Pb"]:
                         if d['filename'] == filename:
                             p = d['priority']
                             if p[0] > global_tracker["Pb"]["max_score"]:
                                 global_tracker["Pb"]["max_score"] = p[0]
                                 global_tracker["Pb"]["max_value"] = p[1]
                                 global_tracker["Pb"]["filename"] = filename
                             elif p[0] == global_tracker["Pb"]["max_score"] and p[1] > global_tracker["Pb"]["max_value"]:
                                 global_tracker["Pb"]["max_value"] = p[1]
                                 global_tracker["Pb"]["filename"] = filename

            # 檔案結算 (PBB/PBDE) - 取單一檔案內最大的值
            for group_key, values in file_group_data.items():
                if values:
                    best_in_file = sorted(values, key=lambda x: (x[0], x[1]), reverse=True)[0]
                    data_pool[group_key].append({
                        "priority": best_in_file,
                        "filename": filename
                    })

        except Exception as e:
            st.warning(f"⚠️ 檔案 {filename} 解析異常: {e}")
        
        progress_bar.progress((i + 1) / len(files))

    # --- 最終聚合 ---
    final_row = {}
    
    # Debug: 顯示 Pb 來源
    if global_tracker["Pb"]["filename"]:
        print(f"Main Report Source (based on Pb): {global_tracker['Pb']['filename']}")

    for key in OUTPUT_COLUMNS:
        if key in ["日期", "檔案名稱"]: continue
        candidates = data_pool.get(key, [])
        if not candidates:
            final_row[key] = "" 
            continue
        
        # 排序邏輯: 優先級 (3>2>1) > 數值大小 > 檔名排序
        best_record = sorted(candidates, key=lambda x: (x['priority'][0], x['priority'][1]), reverse=True)[0]
        final_row[key] = best_record['priority'][2] # 顯示原始字串 (含符號)

    # 日期與檔名決定
    final_date_str = ""
    if all_dates:
        latest_date_record = sorted(all_dates, key=lambda x: x[0], reverse=True)[0]
        final_date_str = latest_date_record[0].strftime("%Y/%m/%d")
    
    final_row["日期"] = final_date_str
    
    # 決定最終檔名：優先使用 Pb 值最高的來源檔名 (主要材料)，若無則用最新日期檔名
    if global_tracker["Pb"]["filename"]:
        final_row["檔案名稱"] = global_tracker["Pb"]["filename"]
    else:
        final_row["檔案名稱"] = latest_date_record[1] if all_dates else (files[0].name if files else "Unknown")

    return [final_row]

# --- 介面 ---
st.set_page_config(page_title="SGS/Intertek 報告聚合工具 v35.0", layout="wide")
st.title("📄 萬用型檢測報告聚合工具 (v35.0 強化版)")
st.markdown("""
**v35.0 更新重點：**
1.  **強化實驗室判斷**：針對 SGS、Intertek、CTI 有更明確的表頭定位邏輯。
2.  **特殊日期支援**：新增支援 `05-Jan-2023` 或 `05-May-2023` 等英文月份格式。
3.  **數值防呆**：避免將 Limit (限值) 或 Unit (單位) 誤判為檢測結果。
""")

uploaded_files = st.file_uploader("請一次選取所有 PDF 檔案 (支援 SGS, Intertek, CTI...)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🔄 開始分析"):
        try:
            result_data = process_files(uploaded_files)
            df = pd.DataFrame(result_data)
            
            # 確保欄位順序
            for col in OUTPUT_COLUMNS:
                if col not in df.columns: df[col] = ""
            df = df[OUTPUT_COLUMNS]

            st.success("✅ 分析完成！")
            st.dataframe(df)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Summary')
            
            st.download_button(
                label="📥 下載 Excel 彙整表",
                data=output.getvalue(),
                file_name=f"RoHS_Summary_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
            # 除錯資訊：顯示每個元素抓到的所有候選值 (可選)
            with st.expander("🔍 查看詳細抓取紀錄 (Debug Info)"):
                st.write("系統抓取到的所有潛在數值（未過濾前）：")
                # 這裡需要重新跑一次流程或修改變數範圍來顯示 data_pool，為簡化介面暫不顯示完整結構
                st.info("若數值有誤，請確認 PDF 是否為掃描檔 (本工具僅支援原生電子檔)。")
                
        except Exception as e:
            st.error(f"系統發生錯誤: {e}")
            st.warning("建議：請檢查是否上傳了加密的 PDF 或純圖片型 PDF。")
