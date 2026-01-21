import pdfplumber
import os
import pandas as pd
import re

# ==========================================
# 核心邏輯區 (V54.2 SGS/CTI + V72.0 Intertek)
# ==========================================

class ReportParser:
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
        核心過濾器：
        1. 抓取 'ND'
        2. 抓取 '數字' (如 7, 143)
        3. 排除 MDL/Limit 常見數字 (2, 5, 10, 100, 1000)
        """
        if not value: return False
        val = value.replace(' ', '').upper()
        
        # 允許 ND
        if val in ['ND', 'N.D.', 'NEGATIVE']: return True
        
        # 嘗試判斷是否為數字
        try:
            # 移除 < 符號 (有時候結果是 <5)
            val_clean = val.replace('<', '')
            float(val_clean)
            
            # 過濾掉常見的 MDL 或 Limit (這是一個啟發式過濾，可根據需要調整)
            # 如果數字剛好是 2, 5, 10, 100, 1000，且不是結果，通常會被視為誤抓
            # 但因為您的 Pb 是 7 和 4，這些不會被過濾
            if val_clean in ['2', '5', '8', '10', '50', '100', '1000']: 
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

    def parse_sgs_cti_logic(self, tables):
        """SGS/CTI 專用邏輯 (V54.2 還原版)：強抓數字，分開 PFOA/PFOS"""
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            for row in clean_table:
                row_str = " ".join(row).upper()
                
                for key, keywords in self.target_map.items():
                    # 避免 PFOA 混入 PFAS
                    if key == 'PFAS_General' and ('PFOA' in row_str or 'PFOS' in row_str):
                        continue

                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        # 從後往前找，抓到第一個符合 is_valid_result 的值
                        for cell in reversed(row):
                            if self.is_valid_result(cell):
                                data[key] = cell
                                break
        return data

    def parse_intertek_logic(self, tables):
        """Intertek 專用邏輯 (V72.0)：定位 Result 欄位"""
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            result_col_idx = -1
            
            for row in clean_table:
                row_upper = [str(c).upper() for c in row]
                
                # 1. 嘗試定位 Result 欄位
                if result_col_idx == -1:
                    for idx, cell in enumerate(row_upper):
                        if "RESULT" in cell: # Intertek 特徵
                            result_col_idx = idx
                            break
                    if result_col_idx != -1: continue # 剛找到表頭，跳下一行

                # 2. 抓取數據
                row_str = " ".join(row_upper)
                for key, keywords in self.target_map.items():
                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        found_val = ""
                        # 策略 A: 如果有定位到欄位，優先抓該欄
                        if result_col_idx != -1 and result_col_idx < len(row):
                            val = row[result_col_idx]
                            if self.is_valid_result(val):
                                found_val = val
                        
                        # 策略 B: 如果策略 A 失敗，回退到通用邏輯 (從後往前找)
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
                # 判斷實驗室
                first_page_text = pdf.pages[0].extract_text() or ""
                lab_type = self.identify_lab(first_page_text)
                
                # 提取所有表格
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables: all_tables.extend(tables)
                
                # 根據實驗室分流
                if lab_type == "INTERTEK":
                    extracted_data = self.parse_intertek_logic(all_tables)
                else:
                    extracted_data = self.parse_sgs_cti_logic(all_tables)
                
                # 回傳結果
                result = {"檔案名稱": filename, "實驗室判斷": lab_type}
                result.update(extracted_data)
                return result
                
        except Exception as e:
            return {"檔案名稱": filename, "實驗室判斷": "Error", "Pb": str(e)}

# ==========================================
# 執行區 (Main Execution)
# ==========================================

def main():
    # 1. 設定讀取資料夾 (預設為當前目錄)
    # 如果您有特定資料夾，請將 '.' 改為 '您的資料夾路徑' (例如 'pdfs/')
    source_folder = '.' 
    
    # 2. 找出所有 PDF
    pdf_files = [f for f in os.listdir(source_folder) if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print("❌ 找不到 PDF 檔案，請確認檔案是否在同一個資料夾內。")
        return

    print(f"🔍 發現 {len(pdf_files)} 個 PDF 檔案，開始分析...\n")
    
    parser = ReportParser()
    all_results = []

    for file in pdf_files:
        path = os.path.join(source_folder, file)
        print(f"正在處理: {file} ...")
        data = parser.process_file(path)
        all_results.append(data)
        
        # 顯示簡單結果在畫面上，讓您安心
        print(f"   -> 鉛(Pb): {data.get('Pb', '')} | PFOA: {data.get('PFOA', '')} | PFOS: {data.get('PFOS', '')}")

    # 3. 轉存 Excel
    df = pd.DataFrame(all_results)
    
    # 調整欄位順序 (美觀用)
    cols = ['檔案名稱', '實驗室判斷', 'Pb', 'Cd', 'Hg', 'Cr6+', 'PFOA', 'PFOS', 'PFAS_General']
    # 確保只有存在的欄位才放入
    final_cols = [c for c in cols if c in df.columns]
    df = df[final_cols]

    output_file = "Report_Result.xlsx"
    df.to_excel(output_file, index=False)
    print(f"\n✅ 處理完成！結果已存檔為: {output_file}")

if __name__ == "__main__":
    main()
