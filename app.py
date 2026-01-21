import pdfplumber
import re
import os

class ReportParser:
    def __init__(self):
        # 定義要抓取的化學物質關鍵字映射
        self.target_map = {
            'Pb': ['Lead', 'Pb', '鉛'],
            'Cd': ['Cadmium', 'Cd', '鎘'],
            'Hg': ['Mercury', 'Hg', '汞'],
            'Cr6+': ['Hexavalent Chromium', 'Cr(VI)', '六價鉻'],
            'PFOA': ['Perfluorooctanoic acid', 'PFOA'], # 精確匹配
            'PFOS': ['Perfluorooctane sulfonic acid', 'PFOS'], # 精確匹配
            'PFAS_General': ['Total Fluorine', 'PFAS'] # 只有明確寫出 PFAS 才抓這裡
        }

    def clean_text(self, text):
        """清理文字，移除換行與多餘空白"""
        if not text: return ""
        return text.replace('\n', ' ').strip()

    def is_valid_result(self, value):
        """
        核心邏輯：判斷是否為有效結果
        1. 允許 'ND' (未檢出)
        2. 允許 純數字 (如 '7', '143', '4.5')
        3. 排除 方法偵測極限(MDL) 如 '2', '5', '10' (需搭配上下文，此處做基礎過濾)
        4. 排除 法規限值 如 '1000', '100'
        """
        val = value.replace(' ', '').upper()
        if val == 'ND': return True
        if val == 'N.D.': return True
        
        # 檢查是否為數字
        try:
            float(val)
            # 簡單過濾掉常見的 MDL 或 Limit 數值 (這部分可根據欄位位置優化)
            if val in ['2', '5', '10', '100', '1000']: 
                return False # 暫時排除這些常見干擾數字，視具體表格欄位而定
            return True
        except ValueError:
            return False

    def identify_lab(self, first_page_text):
        """判斷報告是 SGS, CTI 還是 Intertek"""
        text = first_page_text.upper()
        if "INTERTEK" in text:
            return "INTERTEK"
        elif "SGS" in text:
            return "SGS"
        elif "CTI" in text or "CENTRE TESTING INTERNATIONAL" in text:
            return "CTI"
        return "UNKNOWN"

    def parse_sgs_cti_logic(self, tables):
        """
        【V54.2 邏輯還原】
        針對 SGS/CTI 格式：
        1. 嚴格區分 PFOS / PFOA，不混入 PFAS。
        2. 抓取數字結果 (解決 Pb 遺失問題)。
        """
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            # 清理表格內容
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            
            for row in clean_table:
                row_str = " ".join(row).upper()
                
                # 遍歷目標物質
                for key, keywords in self.target_map.items():
                    # 如果該行包含關鍵字 (且該欄位目前為空，避免覆蓋)
                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        
                        # 排除誤判：例如找 PFAS 時不能抓到 PFOA
                        if key == 'PFAS_General' and ('PFOA' in row_str or 'PFOS' in row_str):
                            continue

                        # 尋找結果值：從後往前找，通常結果在後面
                        # 或者是尋找行中符合 is_valid_result 的值
                        for cell in reversed(row):
                            if self.is_valid_result(cell):
                                data[key] = cell
                                break
        return data

    def parse_intertek_logic(self, tables):
        """
        【V72.0 邏輯整合】
        針對 Intertek 格式：
        1. Intertek 常會有不同的表頭 (Result, MDL, Limit)。
        2. 處理 Intertek 特有的 'Not Detected' 寫法。
        """
        data = {k: "" for k in self.target_map.keys()}
        
        for table in tables:
            clean_table = [[self.clean_text(cell) for cell in row] for row in table]
            
            # 嘗試尋找 Result 欄位的索引 (Intertek 表格通常比較規整)
            result_col_idx = -1
            header_found = False
            
            for row in clean_table:
                row_upper = [c.upper() for c in row]
                
                # 定位表頭
                if not header_found:
                    if "RESULT" in row_upper or "RESULTS" in row_upper:
                        # 找包含 Result 字眼的欄位
                        for idx, cell in enumerate(row_upper):
                            if "RESULT" in cell and "ppm" in cell: # Intertek 常見 Result (ppm)
                                result_col_idx = idx
                                header_found = True
                                break
                        if not header_found: # 如果沒找到帶 ppm 的，就找單純 Result
                             for idx, cell in enumerate(row_upper):
                                if "RESULT" in cell:
                                    result_col_idx = idx
                                    header_found = True
                                    break
                    continue # 剛找到表頭，跳過這一行
                
                # 處理數據行
                row_str = " ".join(row).upper()
                for key, keywords in self.target_map.items():
                    if any(kw.upper() in row_str for kw in keywords) and data[key] == "":
                        # 如果有定位到 Result 欄位，直接抓該欄
                        if result_col_idx != -1 and result_col_idx < len(row):
                            val = row[result_col_idx]
                            if self.is_valid_result(val):
                                data[key] = val
                        else:
                            # 沒定位到欄位，使用通用邏輯 (從後往前找)
                            for cell in reversed(row):
                                if self.is_valid_result(cell):
                                    data[key] = cell
                                    break
        return data

    def process_file(self, pdf_path):
        results = {"File": os.path.basename(pdf_path), "Lab": ""}
        
        with pdfplumber.open(pdf_path) as pdf:
            # 1. 判斷實驗室類型
            first_page_text = pdf.pages[0].extract_text() or ""
            lab_type = self.identify_lab(first_page_text)
            results["Lab"] = lab_type
            
            # 2. 提取所有表格
            all_tables = []
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    all_tables.extend(tables)
            
            # 3. 分流處理 (關鍵差異)
            if lab_type == "INTERTEK":
                # 使用 V72.0 針對 Intertek 優化的邏輯
                extracted_data = self.parse_intertek_logic(all_tables)
            else:
                # 使用 V54.2 針對 SGS/CTI (解決 Pb 遺失與 PFAS 混淆) 的邏輯
                extracted_data = self.parse_sgs_cti_logic(all_tables)
                
            results.update(extracted_data)
            
        return results

# --- 使用範例 ---
# 假設您有一個檔案路徑
# parser = ReportParser()
# result = parser.process_file("1.價啣 S1000-2M.pdf")
# print(result)
