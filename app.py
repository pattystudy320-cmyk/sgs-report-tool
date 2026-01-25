import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
import os

# ==========================================
# 核心邏輯區
# ==========================================

class ReportParserFinal:
    def __init__(self):
        # 定義要抓取的目標 (對應 Excel 欄位)
        self.target_map = {
            'Pb': ['Lead', 'Pb', '鉛'],
            'Cd': ['Cadmium', 'Cd', '鎘'],
            'Hg': ['Mercury', 'Hg', '汞'],
            'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', '六價鉻'],
            'PBB': ['Polybrominated biphenyls', 'PBB', '多溴聯苯', 'Sum of PBBs'], # 抓總和
            'PBDE': ['Polybrominated diphenyl ethers', 'PBDE', '多溴二苯醚', 'Sum of PBDEs'], # 抓總和
            'DEHP': ['Bis(2-ethylhexyl) phthalate', 'DEHP', '鄰苯二甲酸二(2-乙基己基)酯'],
            'DBP': ['Dibutyl phthalate', 'DBP', '鄰苯二甲酸二丁酯'],
            'BBP': ['Butyl benzyl phthalate', 'BBP', '鄰苯二甲酸丁苄酯'],
            'DIBP': ['Diisobutyl phthalate', 'DIBP', '鄰苯二甲酸二異丁酯'],
            'F': ['Fluorine', '氟'],
            'Cl': ['Chlorine', '氯'],
            'Br': ['Bromine', '溴'],
            'I': ['Iodine', '碘'],
            'PFOS': ['Perfluorooctane sulfonic acid', 'PFOS', '全氟辛烷磺酸'],
            'PFOA': ['Perfluorooctanoic acid', 'PFOA', '全氟辛酸'], # PFOA 獨立欄位 (雖然您需求沒特別提，但通常與 PFOS 並列)
            # PFAS 欄位由 "Test Requested" 判定，不從表格抓
        }

    def clean_text(self, text):
        """清理文字"""
        if not text: return ""
        return text.replace('\n', ' ').strip()

    def extract_date(self, text):
        """從第一頁文字中抓取報告日期"""
        if not text: return ""
        # 格式 1: Date: Feb 27, 2025
        match = re.search(r"Date:\s*([A-Za-z]{3}\s+\d{1,2},\s+\d{4})", text, re.IGNORECASE)
        if match: return match.group(1)
        # 格式 2: Date: Oct 10, 2024
        match = re.search(r"Date:\s*([A-Za-z]{3}\s+\d{1,2}\s+\d{4})", text, re.IGNORECASE)
        if match: return match.group(1)
        # 格式 3: Date: 03 Mar 2023
        match = re.search(r"Date:\s*(\d{2}\s+[A-Za-z]{3}\s+\d{4})", text, re.IGNORECASE)
        if match: return match.group(1)
        # 格式 4: 日期: 2024年10月10日
        match = re.search(r"日期[:：]\s*(\d{4}年\d{1,2}月\d{1,2}日)", text)
        if match: return match.group(1)
        return ""

    def check_pfas_requested(self, text):
        """
        檢查 Test Requested 是否包含 PFAS 字串
        規則：有 "PFAS" -> "REPORT", 否則 -> ""
        """
        if not text: return ""
        # 先抓出 Test Requested 區塊 (簡單起見，抓全文搜尋，或抓到 Test Results 之前)
        # 為了避免誤抓後面的 Results，我們只看前 2000 字 (通常 Request 在首頁)
        header_text = text[:3000].upper()
        
        # 尋找 Test Requested 區塊
        if "TEST REQUESTED" in header_text or "檢測要求" in header_text:
             if "PFAS" in header_text:
                 return "REPORT"
        return ""

    def is_valid_result(self, value):
        """判斷值是否有效 (ND 或 數字)"""
        if not value: return False
        val = str(value).replace(' ', '').upper()
        
        # 1. 允許 ND
        if val in ['ND', 'N.D.', 'NEGATIVE', 'NOTDETECTED']: return True
        
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
        """SGS/CTI 抓取邏輯"""
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            for row in clean_table:
                row_str = " ".join(row).upper()
                
                for key, keywords in self.target_map.items():
                    # 特殊處理：鹵素 F, Cl, Br, I 避免誤抓 Total Fluorine 或 PBB/PBDE
                    if key in ['F', 'Cl', 'Br', 'I']:
                        # 鹵素通常比較短，避免抓到長字串 (如 Fluoranthene 包含 F)
                        # 這裡依賴 keywords 的準確性，SGS 表格通常寫 "Fluorine (F)"
                        pass

                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        # 從後往前找結果
                        for cell in reversed(row):
                            if self.is_valid_result(cell):
                                data[key] = cell
                                break
        return data

    def parse_intertek(self, tables):
        """Intertek 抓取邏輯 (找 Result 欄)"""
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
                    # "實驗室": lab_type # 除錯用，可隱藏
                }
                result.update(extracted_data)
                
                # 4. 強制覆蓋 PFAS 欄位邏輯 (依據 Test Requested)
                result['PFAS'] = pfas_status
                
                return result
                
        except Exception as e:
            return {"檔案名稱": filename, "Pb": f"Error: {str(e)}"}

# ==========================================
# 網頁介面區
# ==========================================

st.set_page_config(page_title="SGS/Intertek 報告聚合工具 (最終版)", layout="wide")

st.title("📄 報告聚合工具 (PFAS/鹵素規則更新版)")
st.markdown("""
* **PFAS 規則**：若 Test Requested 包含 "PFAS" 字串 -> 顯示 `REPORT`，否則空白。
* **鹵素規則**：沒測到的項目保持 `空白`。
* **Pb 規則**：抓取 ND 或 數字。
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
        
        # 指定輸出欄位順序
        target_cols = ['檔案名稱', 'DATE', 'Pb', 'Cd', 'Hg', 'Cr6+', 
                       'PBB', 'PBDE', 
                       'DEHP', 'DBP', 'BBP', 'DIBP', 
                       'F', 'Cl', 'Br', 'I', 
                       'PFOS', 'PFAS'] # PFAS 放最後
                       
        # 防呆：只選取存在的欄位
        final_cols = [c for c in target_cols if c in df.columns]
        df = df[final_cols]
        
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
