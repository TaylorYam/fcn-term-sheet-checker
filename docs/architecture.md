# 架構概覽

## 目標與範圍

協助作業人員將 FCN Term Sheet 與已確認的下單資料逐欄比對，輸出可回溯到原文件的例外清單。第一階段先支援一家 issuer 的一個文字型 PDF 範本；不做產品定價、交易執行、法律條款解釋或無人覆核的交易放行。

已實作 BARC／HSBC 文字型 PDF 的核對（Issue #7、#9、#41），以參考條件表批量核對多份說明書並回填 ISIN 與比價日（Issue #43、#46，[ADR 0004](adr/0004-reference-sheet-as-check-source.md)）；OCR 與更多範本仍為目標設計。名詞見 [CONTEXT.md](../CONTEXT.md)。

## 系統資料流

```mermaid
flowchart TD
    A[Term Sheet PDF] --> B[逐頁檢查文字層與擷取品質]
    B -->|可用文字| C[Text extraction]
    B -->|掃描頁或不可用文字層| D[本地 OCR adapter：後續階段]
    C --> E[文件區塊：頁碼／座標／原文]
    D --> E
    E --> F[Issuer 與範本版本辨識]
    F --> G[已知範本 parser]
    G --> H[標準化與驗證]
    O[參考條件表] --> P[參考條件表 adapter 與驗證]
    H --> R[Rule engine]
    P --> R
    R --> S[根目錄 runtime/核對紀錄：整批 JSON]
    R --> W[核對結果檔：回填後（整份通過或人工放行才回填）＋錯誤清單]
    B -->|不支援／失敗| X[待人工覆核]
    F -->|未知／多重命中| X
    G -->|缺漏／歧義| X
    H -->|不合法| X
    P -->|不合法／配對不明| X
    X --> S
```

OCR 尚未實作時，掃描頁直接回報不支援並要求覆核。混合型 PDF 逐頁處理，不因某頁有文字就跳過其他頁。LLM 不在第一版執行路徑內。

## 建議模組邊界

第一階段實際模組如下（OCR adapter 尚未建立）。

| 預定路徑 | 職責 | 邊界 |
|---|---|---|
| `src/fcn_checker/ingestion.py` | 文件 hash、加密／破損檢查；輸入錯誤轉 ERROR 結果、核對紀錄用檔案資訊（檔名、路徑、hash）；輸出檔一律新建 `write_new`（已存在不覆蓋、無法寫入分開回報） | 不解析金融欄位；不嘗試繞過密碼 |
| `src/fcn_checker/extraction.py` | PyMuPDF 逐頁文字行、頁碼、bbox；排除頁碼雜訊 | 輸出頁碼、座標、文字；不判斷核對結果 |
| `src/fcn_checker/issuers.py` | 上手註冊表：每家上手只提供識別資料（代號、範本、名稱、parser 版本、未涵蓋清單）、辨識 `detect`、讀出 `read`（標準欄位＋上手專屬資料）、說明書內部規則 `rules`、規則可讀的參考條件表欄位 `reference_fields`（[ADR 0005](adr/0005-issuer-adapter-and-shared-rules.md)，目前只有 BARC 宣告年利率與天期）；範本辨識 `detect` 零個或多個命中轉人工覆核 | 批量入口、CLI、PANEL 都由此分派；新增上手在此登記，並在 `config/issuer_prefixes.toml` 登記上手編號 |
| `src/fcn_checker/parsers/` | `layout.py` 章／條／子項定位（格式由各上手的 `LayoutSpec` 提供）；`barc.py` 範本辨識與欄位、價格表擷取；`barc_schedule.py` §13 配息表與提前出場表；`hsbc.py`／`hsbc_tables.py` HSBC 專屬欄位與表格 | 使用錨點、座標與有限 regex；多重命中轉歧義，不任選 |
| `src/fcn_checker/schema.py` | 標準化型別：`ParsedField`、`Evidence`、`CheckResult`、`CheckReport` | 缺值／歧義／不合法／不適用分開；保留來源證據；不依賴其他模組 |
| `src/fcn_checker/standard_fields.py` | 說明書標準欄位清單（`STANDARD_FIELDS`）、讀出結果介面 `TermSheet` 與值的形狀（`PriceRow`、`AutocallSchedule`、審查標準規則的出處清單 `Occurrence`） | 上手 parser 與共用規則之間的 seam：各上手 parser 依此交出欄位（含審查標準規則用的欄位），共用規則只經 `read_standard` 讀這些欄位；`TermSheet.f` 不丟例外，沒交出的欄位為 `not_provided` 缺漏 |
| `src/fcn_checker/config.py` | 載入審查標準、參考條件表格式、上手編號對照（TOML）；根目錄設定缺檔時改用程式內建設定 | 會隨時間改變的基準只在設定檔 |
| `src/fcn_checker/orders/reference.py` | 參考條件表 adapter（多列表格，所有上手共用，記下每欄儲存格位置供回填）與 `OrderRecord` | 未知欄名回報覆核；禁止用文件值填補預期值 |
| `src/fcn_checker/rules/` | 版本化規則（rule_id）與明確容差；`kit.py` 為規則共用工具（`Context`／`IssuerContext`、產生結果、缺值轉人工覆核、讀參考條件表的值與轉型、`read_standard`）；`reference.py` 為參考條件表共用規則（表頭欄名檢查、表上事先填好的欄位與標準欄位的比對、Non-Call；空值寫法取自格式設定）；`review_standard.py` 為審查標準規則，只有 `review_standard_rules` 一個進入點；`barc.py`、`hsbc.py` 等為上手專屬的說明書內部規則與未涵蓋清單，只拿到 `IssuerContext` | 不讀檔、不呼叫模型、不自動修改來源值 |
| `src/fcn_checker/backfill.py` | 回填欄位（ISIN Code、發行日、比價日_1～12）整段流程：每格決策與 `backfill.*` 規則（缺欄名轉人工覆核）、寫入呼叫端決定要回填的說明書（整份 PASS 或人工放行）、開檔前比對核對時記錄的參考條件表 hash、回填值寫進記憶體中的工作表（沿用日期格式）、決策的顯示標籤 `BackfillAction.label`（PANEL 使用） | 批量入口 `save_batch` 呼叫；原檔不動 |
| `src/fcn_checker/result_file.py` | 核對結果檔 `<參考條件表檔名>_核對結果_<時間>.xlsx`：「回填後」（原 `樣本清單` 版面，只留整份通過或人工放行且已回填的列，順序照原表；其他工作表不帶入）與「錯誤清單」（每份沒通過也沒人工放行的 PDF 一列：TDCC Code、PDF 檔名、錯訊） | 批量入口 `save_batch` 呼叫；寫到指定資料夾、exclusive create 不覆蓋 |
| `src/fcn_checker/single_check.py` | 單份核對：配對結果 → 表頭欄位檢查 → 參考條件表欄位規則 → 上手說明書內部規則 → 審查標準規則 → Non-Call／ISIN／發行日／比價日 → 回填決策 → 整體狀態 | 所有上手共用同一順序；只用批量入口交來的讀出結果，不重新辨識或讀出；上手說明書內部規則只拿到 `IssuerContext`（含上手宣告的參考條件表欄位），其餘規則拿完整 `Context` |
| `src/fcn_checker/messages.py` | 錯訊：每條問題的中文說明（`problem_message`）與項目名稱（`subject`）；參考條件表欄位寫成「<Excel 欄名>對不起來：參考條件表 <值>／說明書 <值>」，其他類別用規則的中文說明並附雙方值；規則沒寫說明時依原因與狀態給中文預設 | 核對結果檔「錯誤清單」與 PANEL 結果明細共用；不判定哪一邊錯、不顯示 rule_id／reason_code |
| `src/fcn_checker/reporting.py` | 核對紀錄：每次儲存在根目錄（CLI 為執行目錄、PANEL 為安裝根目錄）`runtime/核對紀錄/<時間>.json` 寫一份整批 JSON：程式版本與 commit、設定檔與審查標準的路徑與 hash、參考條件表與每份 PDF 的 hash、每份 PDF 的逐項結果（人工放行仍保留原判定，另記 `manual_release`）、證據與回填決策 | 供維護人員追查，作業人員不需要看；時間戳與核對結果檔相同、不覆蓋；寫入失敗只記整批錯誤，不影響核對結果檔 |
| `src/fcn_checker/batch.py` | 批量入口，分三段：`preview_batch`（唯讀辨識：檔名上手編號、範本辨識、商品代號、對到的列）、`check_batch`（先辨識全部說明書、讀出一次，同一批多份對到同一列的全部轉人工覆核，其餘配對後交單份核對，不寫檔）、`save_batch`（確認參考條件表未變更後，寫核對結果檔與核對紀錄；整份 PASS 或人工放行才回填）；`BatchItem.release_problem` 判斷能否人工放行（回填值確定且不和參考條件表打架），`BatchItem.status` 為人工放行後的有效狀態；`run_batch` = 核對＋儲存 | 測試切點 1；單份失敗不中斷整批；原檔不動、不覆蓋既有檔案 |
| `src/fcn_checker/cli.py` | `fcn-batch` 指令與結束碼 | 測試切點 2；無 Web UI、資料庫或雲端服務 |
| `src/fcn_checker/panel_workflow.py` | PANEL 工作階段：參考條件表＋多份說明書的預覽、核對、人工放行（`release`／`cancel_release`／`release_problem`，只作用於當次結果）、儲存，以及來源與設定檔 hash 失效檢查 | 測試切點 3；呼叫批量入口三段，按儲存才寫檔 |
| `src/fcn_checker/panel.py` | Tkinter 本機視窗：選檔（參考條件表＋多份 PDF）、預覽表、逐份結果與回填決策呈現、人工放行按鈕與確認視窗 | 背景讀檔、主執行緒更新 UI；Windows 啟動前設定 system DPI awareness；不建立網路服務；仍接受舊啟動器的 `--order-formats-dir` |
| `src/fcn_checker/version.py` | 程式版本：`git_revision`（Git 工作目錄的 HEAD）、`program_commit`（正在執行的程式的 commit：開發用 Git，或 `setup_panel.cmd` 安裝時記錄的 `.local/installed.json`，核對紀錄使用）、`record_installation`（安裝時記錄版本） | 公開測試入口 `program_commit`（可指定 `package`）與 `record_installation`；不連網，PANEL 不提供自動更新（ADR 0006） |
| `panel_bootstrap.py` | 雙擊入口（`launch_panel.pyw`）的啟動器：一律從專案根目錄啟動 PANEL，傳入根目錄 config 與安裝根目錄 | 不連網；不讀舊版自動更新留下的 `.local/current.json` |
| `tests/synth.py`、`tests/hsbc_synth.py` | 各上手的說明書合成器：產生合成說明書 PDF 與一致的參考條件表列 | 數值皆虛構；不提交真實客戶交易資料；上手之間互不引用 |
| `tests/pdf_writer.py`、`tests/reference_synth.py`、`tests/harness.py` | 不分上手的測試工具：PDF 寫入、參考條件表合成（發行機構寫法取自格式設定）、單份核對 harness（經 `check_batch`，依 rule_id 取結果） | 新測試用共用 harness 寫 |

PyMuPDF 優先用於文字區塊與座標擷取，pdfplumber 用於表格／版面需要；實際採用順序應由樣本與授權條件評估，首版不必同時依賴兩者。純文字攤平可能破壞欄位關係，應保留列、區塊及跨頁資訊。OCR adapter 待文字流程穩定後加入，不預先綁定引擎。

## 資料與判定

詳見 [資料契約](data-contract.md)。文件及下單資料各自驗證後，依明確交易識別配對；第一個流程可用人工指定的一對檔案，不用相似度猜配對。

每項規則產生 `PASS`、`MISMATCH`、`REVIEW_REQUIRED`、`NOT_APPLICABLE` 或 `ERROR`。只有所有適用必核欄位有可靠值且通過驗證／核對，整體才可為 `PASS`；未實作的必核規則不算通過。整體狀態優先順序為 `ERROR > REVIEW_REQUIRED > MISMATCH > PASS`，核對紀錄仍保留全部差異。`NOT_APPLICABLE` 必須由產品範本與明確規則支持，不能以空值推定。

利率、金額與比例使用 Decimal 概念；序列化為十進位字串，避免二進位浮點誤差。日期不猜日月順序；百分比不混淆年率與每期利率。容差、四捨五入、計息及日期調整規則必須按欄位明定、版本化，預設不使用寬鬆容差。

## 可追溯與部署

首版規劃為本機 Python CLI。執行紀錄包含文件 hash、下單輸入 hash、parser/schema/rule/config 版本、程式 commit、擷取工具版本與執行時間。同輸入與同版本應產生相同的標準化值及判定，時間與 run ID 等 metadata 不列入此保證。

機密輸入與產出只放 `data/`、`runtime/`；console log 以識別碼與錯誤分類為主，詳細證據放根目錄 `runtime/核對紀錄/` 的核對紀錄。保存期限、存取控制及核對紀錄遮蔽在試行前確認。第一版不需 API 金鑰，不會自動載入 `.env`，設定格式在實作時確定。

## 擴充與限制

Issue #13 新增本機 Tkinter PANEL，#14、#15、#17 接上預覽、核對與保存；#46 改為參考條件表＋多份說明書，並刪除 BARC 詢價表流程。唯讀預覽有效後才可核對；每份說明書先確認上手與參考條件表的列，配對失敗的說明書只回報原因、不執行一般條件比對。核對結果先呈現問題，再列通過／不適用項目，保留完整頁碼、原文及參考條件表儲存格；未涵蓋規則獨立列為待處理。

參考條件表、每份 PDF、審查標準與兩個設定檔的 hash 在載入前後、核對前後及結果使用時檢查；任一變更即使預覽與結果失效，儲存前另確認參考條件表與核對時相同。讀檔與失效檢查在背景執行，UI 更新只在主執行緒，核對期間不能重複提交。所有資料仍在本機處理，僅在使用者按儲存後寫入核對結果檔與核對紀錄，沒有 Web UI、資料庫或雲端服務。核對結果檔與核對紀錄一律 exclusive create 禁止覆蓋；寫入失敗會回報原因，不清除當次結果。核對紀錄寫在專案根目錄的 `runtime/`，更新程式時保留。雙擊入口讀專案根目錄的 `config`；直接以 `fcn-panel` 指定的 config 資料夾缺 `reference_sheet.toml`／`issuer_prefixes.toml` 時，才改用程式內建的同名設定。Windows 透過專案獨立 .venv 安裝與雙擊啟動，雙擊入口一律從專案根目錄啟動。

新增 issuer 時加入獨立且版本化的 parser 與對應 fixtures，不把所有文件塞入一組通用 regex。未知格式保留人工覆核入口。步驟、交付物與驗收門檻見[新增上手實作規範](issuer-onboarding.md)；多上手分派已完成（Issue #28）：新上手在 `issuers.py` 登記、在 `config/issuer_prefixes.toml` 登記上手編號後，批量入口、CLI、PANEL 與名稱樣板即依上手運作。上手 adapter 只提供標準欄位與說明書內部規則，參考條件表欄位、審查標準、Non-Call／ISIN／發行日／比價日與回填規則各上手共用（[ADR 0005](adr/0005-issuer-adapter-and-shared-rules.md)）。

未來 LLM fallback 若獲批准，只能作為 extraction adapter 提供候選欄位與來源證據；不得修改預期下單值或取代 rule engine。需另立 ADR、資料傳送政策與驗證門檻；第一版無相關 SDK、開關或外部呼叫。

決策：[0001：第一版採規則式核對](adr/0001-deterministic-runtime.md)、[0002：本機 Python CLI，PDF 擷取採用 PyMuPDF](adr/0002-python-cli-pymupdf.md)、[0004：核對條件統一改用參考條件表](adr/0004-reference-sheet-as-check-source.md)、[0005：上手 adapter 只提供標準欄位與說明書內部規則，參考條件表與審查標準規則各上手共用](adr/0005-issuer-adapter-and-shared-rules.md)。

更新方式：[0006：PANEL 只供維護者使用，移除自動更新](adr/0006-panel-maintainer-only-no-self-update.md)，以 `git pull`＋`setup_panel.cmd` 更新（取代 [0003](adr/0003-public-github-panel-update.md)）。程式更新與審查設定導入仍分開。
