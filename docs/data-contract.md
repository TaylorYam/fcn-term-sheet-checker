# 現行資料契約

核實日期：2026-10-18。依目前 Python 型別與 JSON 序列化描述，不是另外維護的 JSON Schema。來源為 [`schema.py`](../src/fcn_checker/schema.py)、[`standard_fields.py`](../src/fcn_checker/standard_fields.py)、[`investor_sheet.py`](../src/fcn_checker/investor_sheet.py)、[`orders/reference.py`](../src/fcn_checker/orders/reference.py) 與 [`reporting.py`](../src/fcn_checker/reporting.py)。

## 1. 輸入、快照與證據

一批輸入是一份參考條件表 Excel 與多份 PDF。同商品的 `_TS.pdf`、`_IIS.pdf` 一起提供，兩份均通過或人工放行才回填。表頭、必要欄、欄名別名與配對見[參考條件表格式](order-formats/reference-sheet.md)。

| 型別 | 現行內容 |
|---|---|
| `SourceSnapshot` | `files` 保存來源路徑與 SHA-256。設定在載入前、Excel 與 PDF 在預覽讀取前取快照；核對、放行、儲存與匯出會確認來源未變 |
| `Line` | `page`、`x0/y0/x1/y1`、`text`、可用時的 `char_spans`。頁碼從 1 起，座標為 PyMuPDF 左上原點的 pt |
| `Evidence` | 實際只有 `page`、`bbox`、`text`；bbox 序列化為四個數字的陣列，沒有 OCR 分數或擷取品質欄位 |
| `OrderRecord` | 參考條件表的一列，提供商品代號、來源列及標準欄位的值。來源檔 hash 另存快照與 metadata |
| `OrderValue` | `value`、儲存格出處 `source`、Excel 欄名 `column` |

正式流程使用 PyMuPDF 擷取文字層，沒有獨立的 `Document` schema 或 OCR adapter。圖像內容不會被 OCR 補讀。

## 2. 讀出欄位與標準資料

`ParsedField` 包含 `name`、`status`、`value`、`evidence`、`candidates`、`note`。

| 狀態 | 意義 |
|---|---|
| `present` | 已讀出可使用的值 |
| `missing` | 缺漏或 adapter 未交出資料 |
| `ambiguous` | 多個不同候選，保留候選與證據 |
| `invalid` | 不符合欄位語意或格式 |
| `not_applicable` | 範本明確沒有此欄位或不適用 |

`standard_fields.not_provided(name)` 表示 adapter 未交出，相關規則轉人工覆核；`absent(name, 說明)` 才表示明確不適用，不能以抓不到來推定。

說明書結果遵循 `TermSheet`：`f(name)` 交出 `ParsedField`，`full_text` 為全文索引。完整欄位清單在 `STANDARD_FIELDS`；上手可另帶專屬資料，共用規則不讀專屬解析型別。除了商品條件，也包含：

- `PriceRow`：同一標的的價格、名稱與幣別；彭博代號含交易所碼，配對順序依設定。
- `AutocallSchedule`：第一個可提前出場期及比價日序列，供 Non-Call 與回填。
- `DateTable`／`DateRow`／`PeriodStart`：日期表、各期日期、證據與期始日定義，交給共用日期規則。
- `Occurrence`：同一條件的多處出處與值，名稱依出處代號由 `OCCURRENCE_NAMES` 查出。
- `ScenarioParam`／`ScenarioPrice`／`Money`：情境參數、情境價格與金額旁的幣別，交給共用情境規則；不表示所有情境算式都會重算。

投資人須知 `IisSheet` 有 `fields`、`full_text`、`provided`；`provides(name)` 查詢範本宣告。宣告卻讀不到時必須回報，不能從「讀到什麼」反推核對範圍。共用入口是 `investor_sheet.read_iis`。

數值使用 `Decimal`；百分比保留百分比數字（`75` 代表 75%，不是 `0.75`），日期使用 `datetime.date`。讀 Excel 數字先清浮點尾數；容差與四捨五入依[共用規則](rules/shared-check-rules.md)及上手規格，不猜日期或單位。

## 3. 辨識與規則結果

`Identification` 保存每份 PDF 的文件種類、上手、商品代號、參考條件表列、範本／讀出結果、同商品另一份、是否執行規則與唯一的 `PairingProblem`。全部文件辨識與分組後凍結，核對沿用、不重新讀 PDF。`checked` 決定是否執行條件規則；缺同商品另一份時仍可核對現有文件，但配對問題會阻止放行與回填。支援範本與樣態黑名單見[支援範圍](support-matrix.md)。

`CheckResult` 實際欄位為 `rule_id`、`rule_version`、`field`、`status`、`expected`、`actual`、`tolerance`、`reason_code`、`message`、`document_evidence`、`order_source`、必填 `item` 及文件種類 `document`。Excel 證據是儲存格字串清單 `order_source`，不是草案的 `order_evidence`；欄位名稱是 `field`，不是 `field_path`。

`Item` 的中文名稱與預期值出處用來組錯訊；來源包括 `reference`、`reference_derived`、`standard`、`expected`、`term_sheet`、`none`，`columns` 保存 Excel 欄名。錯訊不由規則代碼反推名稱。

狀態為 `PASS`、`MISMATCH`、`REVIEW_REQUIRED`、`NOT_APPLICABLE`、`ERROR`。整體優先序 `ERROR > REVIEW_REQUIRED > MISMATCH > PASS`；PANEL 顯示順序則是 ERROR、不一致、人工覆核、通過、不適用，兩者用途不同。

`CheckReport` 包含 `status`、`template`、`results`、`not_covered`、`metadata`、`backfill`、`backfill_certain`。未涵蓋範圍不算已核對；回填確定性是內部值，不寫進紀錄。

## 4. 回填與人工放行

`CellDecision` 有 `column`、`cell`、`sheet_value`、`expected`、`action`，動作為 `fill`、`match`、`mismatch`、`overwrite`。只有 VWAP 價格按規則覆寫，其他回填欄已有不同值時保留原值並阻擋回填。

`BatchOutcome` 保存核對與當次人工放行；放行不改原始判定，只影響能否視為通過。兩份均通過或放行才回填；配對問題、回填不確定或衝突不能越過。現行紀錄只有 `manual_release` 旗標，沒有操作者、放行時間與理由的完整簽核欄位。

`saving.save_batch` 回傳 `SaveReceipt`：狀態、輸出與紀錄路徑、實際回填項目、寫檔錯誤及取消狀態。核對結果不因儲存而改寫。Excel 的「回填後」是固定匯入格式，另一頁為「錯誤清單」，原始檔不修改或覆蓋。

## 5. JSON 核對紀錄

`reporting.RECORD_VERSION = 2`；最外層包含 `record_version`、`saved_at`、`status`、`result_file`、`metadata`、`errors`、`items`。

每份文件記錄檔名、種類、另一份檔名、上手、商品代號、參考列、`filled`、`manual_release`、未回填原因與單份報告。逐條規則記錄 `field`、`order_source`、項目 `column` 與 `item`；`Decimal` 為十進位字串，日期為 ISO 字串，證據含頁碼、bbox 與原文。

`runtime/核對紀錄/<YYYYMMDD-HHMMSS>.json` 與同次 Excel 共用時間戳，禁止覆蓋。來源變更或讀不到時兩份都不寫；其他寫檔錯誤依收據回報，紀錄失敗不代表 Excel 沒寫成。回報包沿用相同序列化格式輸出未通過部分。

真實輸入與紀錄只放 Git 忽略的 `data/`、`runtime/`，不進公開文件或 CI。
