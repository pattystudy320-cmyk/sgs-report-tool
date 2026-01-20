import re
from pypdf import PdfReader

def extract_and_check_rohs_pfas(pdf_path):
    """
    從 PDF 測試報告中提取特定的 RoHS/PFAS 物質檢測結果，並檢查 PFAS 區段標題。
    """
    reader = PdfReader(pdf_path)
    full_text = ""
    for page in reader.pages:
        # 提取每一頁的文字內容
        full_text += page.extract_text() + "\\n"

    # --- 1. 擷取特定物質結果 (RoHS II + PFOS) ---
    
    # 定義要尋找的物質名稱及其可能的報告名稱變體或關鍵字
    # 使用字典將標準名稱映射到報告中可能出現的關鍵字或長名稱
    substance_keywords = {
        'Pb': ['Lead (Pb) Content', '鉛含量'],
        'Cd': ['Cadmium (Cd) Content', '鎘含量'],
        'Hg': ['Mercury (Hg) Content', '汞含量'],
        'Cr6+': ['Chromium VI (Cr(VI)) Content', '六價鉻含量'],
        'DEHP': ['Di\\(2-ethylhexyl\\) Phthalate \\(DEHP\\)', '鄰苯二甲酸二\\(2-乙基己基\\)酯'],
        'BBP': ['Benzyl Butyl Phthalate \\(BBP\\)', '鄰苯二甲酸苯基丁酯'],
        'DBP': ['Dibutyl Phthalate \\(DBP\\)', '鄰苯二甲酸二丁酯'],
        'DIBP': ['Diisobutyl Phthalate \\(DIBP\\)', '鄰苯二甲酸二異丁酯'],
        'PFOS': ['Perfluorooctanesulfonate \\(PFOS\\)', '全氟辛磺酸'],
    }
    
    # 處理 PBBs 和 PBDEs 總和的特別邏輯
    # 尋找 "Polybrominated Biphenyls (PBBs) 多溴聯苯" 後面的 ND 或數值
    pbfs_match = re.search(r"Polybrominated Biphenyls \(PBBs\).*?(ND|\d+)", full_text, re.DOTALL)
    if pbfs_match:
        results = {'PBBs_Total': pbfs_match.group(1).strip()}
    else:
        results = {'PBBs_Total': 'Not Found'}

    # 尋找 "Polybrominated Diphenyl Ethers (PBDEs) 多溴聯苯醚" 後面的 ND 或數值
    pbdes_match = re.search(r"Polybrominated Diphenyl Ethers \(PBDEs\).*?(ND|\d+)", full_text, re.DOTALL)
    if pbdes_match:
        results['PBDEs_Total'] = pbdes_match.group(1).strip()
    else:
        results['PBDEs_Total'] = 'Not Found'

    # 針對其他單一物質的尋找
    for substance_short_name, search_terms in substance_keywords.items():
        found_value = "Not Found"
        for term in search_terms:
            # 嘗試尋找該物質名稱後的第一個「值」(數字、ND、Negative、N.D.)
            # 使用正則表達式捕獲關鍵字後可能的值
            pattern = re.compile(rf"{term}.*?(ND|N\.D\.|Negative|\d+\.\d+|\d+)", re.DOTALL | re.IGNORECASE)
            match = pattern.search(full_text)

            if match:
                value = match.group(1).strip()
                # 您的優先級：數字 > Negative > N.D./ND
                # 這裡只要找到就記錄，因為報告結構中ND/數值出現在固定位置
                found_value = value
                break
        
        results[substance_short_name] = found_value
    
    # --- 2. 檢查 PFAS 區段標題 ---
    pfas_section_keywords = [
        "Per- and Polyfluoroalkyl Substances",
        "全氟/多氟烷基物質",
    ]
    
    pfas_status = "Not Found"
    for keyword in pfas_section_keywords:
        if re.search(re.escape(keyword), full_text, re.IGNORECASE):
            pfas_status = "REPORT"
            break
            
    results['PFAS_Section_Status'] = pfas_status
    
    return results

# 執行範例
file_path = 'test_report.pdf' 
all_results = extract_and_check_rohs_pfas(file_path)

print("--- 綜合測試結果 ---")
for item, result in all_results.items():
    print(f"{item}: {result}")
