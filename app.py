import pdfplumber
import os
import pandas as pd
import re

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
        if val in ['ND', 'N.D.', 'NEGATIVE', 'Not Detected']: return True
        
        # 2. 嘗試判斷是否為數字
        try:
            # 移除 < 符號 (有時候結果是 <5)
            val_clean = val.replace('<', '')
            float(val_clean)
            
            # [V54.2 重點] 過濾掉常見的 MDL 或 Limit，避免抓錯
            # 排除 2, 5, 10 (常見MDL) 和 100, 1000 (常見限值)
            # 但保留其他數字 (如您的 7, 4, 143)
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
        """
        ★ [V54.2 邏輯還原區] - 針對 SGS/CTI 報告
        1. 強制抓取數字 (解決 Pb 遺失)
        2. 嚴格區分 PFOA / PFOS (解決 PFAS 混亂)
        """
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            for row in clean_table:
                row_str = " ".join(row).upper()
                
                for key, keywords in self.target_map.items():
                    # [V54.2 修正] 避免 PFOA/PFOS 被誤判為 PFAS
                    if key == 'PFAS_General' and ('PFOA' in row_str or 'PFOS' in row_str):
                        continue

                    # [V54.2 修正] 只有當該行包含關鍵字，且欄位尚未填值時才抓
                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        # 從後往前找，抓到第一個符合 is_valid_result 的值
                        # 這能有效避開前面的 MDL (2) 或 Limit (1000)
                        for cell in reversed(row):
                            if self.is_valid_result(cell):
                                data[key] = cell
                                break
        return data

    def parse_intertek_v72_0(self, tables):
        """
        [V72.0 邏輯] - 針對 Intertek 報告
        利用 Result 欄位定位，避免抓錯
        """
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

    def process_file(self, file_path):
        filename = os.path.basename(file_path)
        try:
            with pdfplumber.open(file_path) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
                lab_type = self.identify_lab(first_page_text)
                
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables: all_tables.extend(tables)
                
                # ★ 關鍵分流：SGS 用 V54.2，Intertek 用 V72.0
                if lab_type == "INTERTEK":
                    extracted_data = self.parse_intertek_v72_0(all_tables)
                else:
                    extracted_data = self.parse_sgs_cti_v54_2(all_tables)
                
                result = {"檔案名稱": filename, "實驗室": lab_type}
                result.update(extracted_data)
                return result
                
        except Exception as e:
            return {"檔案名稱": filename, "實驗室": "Error", "Pb": str(e)}

# ==========================================
# 執行程式
# ==========================================
if __name__ == "__main__":
    # 設定讀取當前目錄下的 PDF
    source_folder = '.' 
    pdf_files = [f for f in os.listdir(source_folder) if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print("❌ 錯誤：找不到 PDF 檔案，請確認檔案與程式在同一資料夾。")
    else:
        print(f"🔍 發現 {len(pdf_files)} 個 PDF，開始使用 V54.2/V72.0 混合邏輯分析...\n")
        
        parser = ReportParserV54()
        all_results = []

        for file in pdf_files:
            print(f"正在處理: {file} ...")
            data = parser.process_file(os.path.join(source_folder, file))
            all_results.append(data)

        # 輸出 Excel
        df = pd.DataFrame(all_results)
        cols = ['檔案名稱', '實驗室', 'Pb', 'Cd', 'Hg', 'Cr6+', 'PFOA', 'PFOS', 'PFAS_General']
        df = df[[c for c in cols if c in df.columns]]
        
        output_file = "Result_V54_2.xlsx"
        df.to_excel(output_file, index=False)
        print(f"\n✅ 成功！報告已產出: {output_file}")
