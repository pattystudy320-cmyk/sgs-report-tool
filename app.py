import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
import os

# ==========================================
# 核心邏輯區 (V54.2 SGS/CTI + V72.0 Intertek + 日期/PFAS 增強版)
# ==========================================

class ReportParserFinal:
    def __init__(self):
        # 定義要抓取的目標 (對應 Excel 欄位)
        self.target_map = {
            'Pb': ['Lead', 'Pb', '鉛'],
            'Cd': ['Cadmium', 'Cd', '鎘'],
            'Hg': ['Mercury', 'Hg', '汞'],
            'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', '六價鉻'],
            'PBB': ['Polybrominated biphenyls', 'PBB', '多溴聯苯', 'Sum of PBBs'],
            'PBDE': ['Polybrominated diphenyl ethers', 'PBDE', '多溴二苯醚', 'Sum of PBDEs'],
            'DEHP': ['Bis(2-ethylhexyl) phthalate', 'DEHP', '鄰苯二甲酸二(2-乙基己基)酯'],
            'DBP': ['Dibutyl phthalate', 'DBP', '鄰苯二甲酸二丁酯'],
            'BBP': ['Butyl benzyl phthalate', 'BBP', '鄰苯二甲酸丁苄酯'],
            'DIBP': ['Diisobutyl phthalate', 'DIBP', '鄰苯二甲酸二異丁酯'],
            'F': ['Fluorine', '氟'],
            'Cl': ['Chlorine', '氯'],
            'Br': ['Bromine', '溴'],
            'I': ['Iodine', '碘'],
            'PFOS': ['Perfluorooctane sulfonic acid', 'PFOS', '全氟辛烷磺酸'],
            'PFOA': ['Perfluorooctanoic acid', 'PFOA', '全氟辛酸'],
            # PFAS 欄位由 "Test Requested" 判定
        }

    def clean_text(self, text):
        """清理文字"""
        if not text: return ""
        return text.replace('\n', ' ').strip()

    def extract_date(self, text):
        """
        [增強版日期抓取]
        層次 1: 抓取 Date: 後面的標準格式
        層次 2: 抓取 Date: 後面的特殊格式 (如 06-Jan-2025)
        層次 3: 若找不到 Date，抓取 Testing Period 的結束日期
        """
        if not text: return ""
        
        # 層次 1 & 2: 針對 "Date:" 關鍵字的抓取
        # 格式 A: Feb 27, 2025 (SGS 常見)
        match = re.search(r"Date\s*[:：]\s*([A-Za-z]{3}\s+\d{1,2},?\s+\d{4})", text, re.IGNORECASE)
        if match: return match.group(1)
        
        # 格式 B: 06-Jan-2025 (您提供的特殊 SGS 報告)
        match = re.search(r"Date\s*[:：]\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", text, re.IGNORECASE)
        if match: return match.group(1)
        
        # 格式 C: 2024/10/10 or 2024.10.10
        match = re.search(r"Date\s*[:：]\s*(\d{4}[\/\.]\d{1,2}[\/\.]\d{1,2})", text, re.IGNORECASE)
        if match: return match.group(1)

        # 格式 D: 中文日期
        match = re.search(r"日期\s*[:：]\s*(\d{4}年\d{1,2}月\d{1,2}日)", text)
        if match: return match.group(1)

        # 層次 3: 備援機制 - 抓取 Testing Period 的結束日
        # 尋找 Testing Period 區塊，並抓取該行最後一個出現的日期格式
        if "Testing Period" in text or "測試期間" in text:
            # 抓取 Testing Period 後面的一段文字
            period_match = re.search(r"(Testing Period|測試期間).*?(\d{1,2}-[A-Za-z]{3}-\d{4})", text, re.IGNORECASE | re.DOTALL)
            if period_match:
                # 這裡會抓到最後一個匹配的日期 (即結束日)
                return period_match.group(2)
                
        return ""

    def check_pfas_requested(self, text):
        """
        檢查 Test Requested 是否包含 PFAS 字串
        規則：有 "PFAS" -> "REPORT", 否則 -> ""
        """
        if not text: return ""
        # 只看前 3000 字 (通常 Request 在首頁)
        header_text = text[:3000].upper()
        
        # 尋找 Test Requested 區塊
        if "TEST REQUESTED" in header_text or "檢測要求" in header_text:
             if "PFAS" in header_text:
                 return "REPORT"
        return ""

    def is_valid_result(self, value):
        """
        判斷值是否有效
        1. 允許 ND / Negative
        2. 允許 數字 (解決 Pb=7 消失問題)
        3. 過濾 MDL/Limit 常見數字 (避免抓錯)
        """
        if not value: return False
        val = str(value).replace(' ', '').upper()
        
        # 1. 允許 ND
        if val in ['ND', 'N.D.', 'NEGATIVE', 'NOTDETECTED', 'NOT DETECTED']: return True
        
        # 2. 嘗試判斷是否為數字
        try:
            val_clean = val.replace('<', '')
            float(val_clean)
            # 過濾常見 MDL/Limit
            if val_clean in ['2', '5', '8', '10', '50', '100', '1000', '0.010', '0.025']: 
                return False 
            return True
        except ValueError:
            return False

    def identify_lab(self, first_page_text):
        text = first_page_text.upper()
        if "INTERTEK" in text: return "INTERTEK"
        return "SGS/CTI"

    def parse_sgs_cti(self, tables):
        """SGS/CTI 抓取邏輯 (反向搜尋法)"""
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            for row in clean_table:
                row_str = " ".join(row).upper()
                
                for key, keywords in self.target_map.items():
                    # 避免 PFOA 混入 PFAS 欄位 (雖然 PFAS 已經獨立處理，但以防萬一)
                    if key == 'PFAS_General': continue

                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        # 從後往前找結果
                        for cell in reversed(row):
                            if self.is_valid_result(cell):
                                data[key] = cell
                                break
        return data

    def parse_intertek(self, tables):
        """Intertek 抓取邏輯 (欄位定位法)"""
        data = {k: "" for k in self.target_map.keys()}
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            result_col_idx = -1
            
            for row in clean_table:
                row_upper = [str(c).upper() for c in row]
                if result_col_idx == -1:
                    for idx, cell in enumerate(row_upper):
                        if "RESULT" in cell:
                            result_col_idx = idx
                            break
                    if result_col_idx != -1: continue 

                row_str = " ".join(row_upper)
                for key, keywords in self.target_map.items():
                    if key == 'PFAS_General': continue # Intertek 的 PFAS 也統一用 Requested 判斷

                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        found_val = ""
                        if result_col_idx != -1 and result_col_idx < len(row):
                            val = row[result_col_idx]
                            if self.is_valid_result(val): found_val = val
                        
                        if not found_val:
                            for cell in reversed(row):
                                if self.is_valid_result(cell):
                                    found_val = cell
                                    break
                        if found_val: data[key] = found_val
        return data

    def process_file_stream(self, uploaded_file):
        filename = uploaded_file.name
        try:
            with pdfplumber.open(uploaded_file) as pdf:
                first_page = pdf.pages[0]
                first_page_text = first_page.extract_text() or ""
                
                # 1. 抓取基本資訊
                lab_type = self.identify_lab(first_page_text)
                report_date = self.extract_date(first_page_text)
                pfas_status = self.check_pfas_requested(first_page_text)
                
                # 2. 抓取表格數據
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables: all_tables.extend(tables)
                
                if lab_type == "INTERTEK":
                    extracted_data = self.parse_intertek(all_tables)
                else:
                    extracted_data = self.parse_sgs_cti(all_tables)
                
                # 3. 整合結果
                result = {
                    "檔案名稱": filename,
                    "DATE": report_date,
                }
                result.update(extracted_data)
                
                # 4. 強制覆蓋 PFAS 欄位邏輯 (依據 Test Requested)
                result['PFAS_General'] = pfas_status
                
                return result
                
        except Exception as e:
            return {"檔案名稱": filename, "Pb": f"Error: {str(e)}"}

# ==========================================
# 網頁介面區
# ==========================================

st.set_page_config(page_title="SGS/Intertek 報告聚合工具 (最終版)", layout="wide")

st.title("📄 報告聚合工具 (日期增強版)")
st.markdown("""
**本次更新重點：**
1. **日期抓取**：支援 `06-Jan-2025` 格式，若無 Date 則抓取測試週期結束日。
2. **PFAS 規則**：Test Requested 有 "PFAS" 字串才顯示 `REPORT`，否則空白。
3. **數值與鹵素**：Pb 抓取數字，鹵素沒測顯示空白。
""")

uploaded_files = st.file_uploader("請上傳 PDF 檔案", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 開始分析"):
        st.info("處理中...")
        parser = ReportParserFinal()
        all_results = []
        progress_bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            data = parser.process_file_stream(file)
            all_results.append(data)
            progress_bar.progress((i + 1) / len(uploaded_files))
            
        st.success("完成！")
        
        df = pd.DataFrame(all_results)
        
        # 指定輸出欄位順序 (對應 Excel)
        target_cols = ['檔案名稱', 'DATE', 'Pb', 'Cd', 'Hg', 'Cr6+', 
                       'PBB', 'PBDE', 
                       'DEHP', 'DBP', 'BBP', 'DIBP', 
                       'F', 'Cl', 'Br', 'I', 
                       'PFOS', 'PFAS_General'] # 對應到 Excel 的 PFAS 欄位
                       
        # 防呆：只選取存在的欄位
        final_cols = [c for c in target_cols if c in df.columns]
        df = df[final_cols]
        
        # 為了 Excel 顯示漂亮，將 PFAS_General 改名為 PFAS
        df = df.rename(columns={'PFAS_General': 'PFAS'})
        
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Report Data')
            
        st.download_button(
            label="📥 下載 Excel",
            data=buffer.getvalue(),
            file_name="Report_Result_Final.xlsx",
            mime="application/vnd.ms-excel"
        )
