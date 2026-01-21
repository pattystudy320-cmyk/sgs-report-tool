import streamlit as st
import pdfplumber
import pandas as pd
import io

# ==========================================
# 1. 核心邏輯區 (V54.2 SGS/CTI + V72.0 Intertek)
# ==========================================

class ReportParserV54:
    def __init__(self):
        # 定義要抓取的目標
        self.target_map = {
            'Pb': ['Lead', 'Pb', '鉛'],
            'Cd': ['Cadmium', 'Cd', '鎘'],
            'Hg': ['Mercury', 'Hg', '汞'],
            'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', '六價鉻'],
            'PFOA': ['Perfluorooctanoic acid', 'PFOA'], # 精確匹配 PFOA
            'PFOS': ['Perfluorooctane sulfonic acid', 'PFOS'], # 精確匹配 PFOS
            'PFAS_General': ['Total Fluorine', 'PFAS'] # 只有寫 Total Fluorine 或 PFAS 才抓
        }

    def clean_text(self, text):
        """清理文字"""
        if not text: return ""
        return text.replace('\n', ' ').strip()

    def is_valid_result(self, value):
        """
        [V54.2 核心邏輯]：判斷值是否有效
        1. 抓取 'ND', 'N.D.', 'NEGATIVE'
        2. 抓取 '數字' (關鍵修正：解決 Pb=7, Pb=4 消失的問題)
        3. 自動過濾 MDL/Limit 常見干擾數字
        """
        if not value: return False
        val = str(value).replace(' ', '').upper()
        
        # 1. 允許 ND
        if val in ['ND', 'N.D.', 'NEGATIVE', 'NOT DETECTED']: return True
        
        # 2. 嘗試判斷是否為數字
        try:
            # 移除 < 符號 (有時候結果是 <5)
            val_clean = val.replace('<', '')
            float(val_clean)
            
            # [V54.2 重點] 過濾掉常見的 MDL 或 Limit，避免抓錯
            if val_clean in ['2', '5', '8', '10', '50', '100', '1000', '0.010', '0.025']: 
                return False 
            return True
        except ValueError:
            return False

    def identify_lab(self, first_page_text):
        """自動判斷實驗室"""
        text = first_page_text.upper()
        if "INTERTEK" in text:
            return "INTERTEK"
        elif "SGS" in text:
            return "SGS"
        elif "CTI" in text or "CENTRE TESTING INTERNATIONAL" in text:
            return "CTI"
        return "SGS" # 預設使用 SGS 邏輯

    def parse_sgs_cti_v54_2(self, tables):
        """[V54.2 邏輯] SGS/CTI: 強制抓數字，分開 PFOA/PFOS"""
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            for row in clean_table:
                row_str = " ".join(row).upper()
                
                for key, keywords in self.target_map.items():
                    # [V54.2 修正] 避免 PFOA/PFOS 被誤判為 PFAS
                    if key == 'PFAS_General' and ('PFOA' in row_str or 'PFOS' in row_str):
                        continue

                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        # 從後往前找
                        for cell in reversed(row):
                            if self.is_valid_result(cell):
                                data[key] = cell
                                break
        return data

    def parse_intertek_v72_0(self, tables):
        """[V72.0 邏輯] Intertek: 利用 Result 欄位定位"""
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            result_col_idx = -1
            
            for row in clean_table:
                row_upper = [str(c).upper() for c in row]
                
                # 1. 嘗試定位 Result 欄位
                if result_col_idx == -1:
                    for idx, cell in enumerate(row_upper):
                        if "RESULT" in cell:
                            result_col_idx = idx
                            break
                    if result_col_idx != -1: continue 

                # 2. 抓取數據
                row_str = " ".join(row_upper)
                for key, keywords in self.target_map.items():
                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        found_val = ""
                        # 策略 A: 優先抓定位到的 Result 欄
                        if result_col_idx != -1 and result_col_idx < len(row):
                            val = row[result_col_idx]
                            if self.is_valid_result(val):
                                found_val = val
                        
                        # 策略 B: 沒定位到則回退通用邏輯
                        if not found_val:
                            for cell in reversed(row):
                                if self.is_valid_result(cell):
                                    found_val = cell
                                    break
                        
                        if found_val:
                            data[key] = found_val
        return data

    def process_file_stream(self, uploaded_file):
        """處理 Streamlit 上傳的檔案物件"""
        filename = uploaded_file.name
        try:
            with pdfplumber.open(uploaded_file) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
                lab_type = self.identify_lab(first_page_text)
                
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables: all_tables.extend(tables)
                
                if lab_type == "INTERTEK":
                    extracted_data = self.parse_intertek_v72_0(all_tables)
                else:
                    extracted_data = self.parse_sgs_cti_v54_2(all_tables)
                
                result = {"檔案名稱": filename, "實驗室判斷": lab_type}
                result.update(extracted_data)
                return result
                
        except Exception as e:
            return {"檔案名稱": filename, "實驗室判斷": "Error", "Pb": f"讀取錯誤: {str(e)}"}

# ==========================================
# 2. Streamlit 網頁介面區 (Frontend UI)
# ==========================================

# 設定網頁標題與寬度
st.set_page_config(page_title="SGS/Intertek 報告聚合工具", layout="wide")

st.title("📄 萬用型檢測報告聚合工具 (V91.0 - V54.2邏輯核心)")
st.markdown("""
**功能說明：**
1. 針對 **SGS/CTI**：使用 V54.2 邏輯，修復鉛(Pb)數值漏抓問題，並精確區分 PFOA/PFOS。
2. 針對 **Intertek**：使用 V72.0 邏輯，自動對齊 Result 欄位。
""")

# 檔案上傳區
uploaded_files = st.file_uploader("請拖曳 PDF 檔案到此處 (可多選)", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 開始分析"):
        st.info(f"正在處理 {len(uploaded_files)} 份報告，請稍候...")
        
        parser = ReportParserV54()
        all_results = []
        
        # 進度條
        progress_bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            # 處理單一檔案
            data = parser.process_file_stream(file)
            all_results.append(data)
            # 更新進度
            progress_bar.progress((i + 1) / len(uploaded_files))
            
        st.success("✅ 分析完成！")
        
        # 轉換為 DataFrame
        df = pd.DataFrame(all_results)
        
        # 調整欄位順序
        cols = ['檔案名稱', '實驗室判斷', 'Pb', 'Cd', 'Hg', 'Cr6+', 'PFOA', 'PFOS', 'PFAS_General']
        # 只保留存在的欄位
        final_cols = [c for c in cols if c in df.columns]
        df = df[final_cols]
        
        # 顯示資料表格
        st.dataframe(df, use_container_width=True)
        
        # 製作 Excel 下載按鈕
        # 使用 BytesIO 在記憶體中建立 Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Report Data')
            
        st.download_button(
            label="📥 下載 Excel 報告",
            data=buffer.getvalue(),
            file_name="Report_Result_V54_2.xlsx",
            mime="application/vnd.ms-excel"
        )
