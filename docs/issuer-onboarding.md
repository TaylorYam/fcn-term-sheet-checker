# 新增上手（issuer）實作規範

新增一家上手（發行機構）的說明書範本與下單資料格式時，依本文件的步驟、交付物與驗收門檻進行。流程整理自 BARC 的實際經驗（範本規格、核對規則、Issue #7、#9）。

- 適用：新增上手，或同一上手的新範本版本（例如說明書改版、詢價表改版）。
- 通用開發流程（Issue → branch → 實作 → PR）見 [AGENTS.md](../AGENTS.md)；本文件補充新增上手特有的步驟。
- 參考範例：BARC 的[範本規格](templates/barc-zh-product-description.md)、[詢價格式](order-formats/barc-inquiry.md)、[核對規則](rules/barc-check-rules.md)、[審查標準](rules/review-standard.md)。

## 0. 總覽

| 步驟 | 誰 | 產出 | 進 Git |
|---|---|---|---|
| 1. 準備樣本 | 作業人員 | 真實說明書、詢價表、SOP 特殊規定 | 否（`data/`） |
| 2. 探勘 | 開發者（AI）＋作業人員確認 | 探勘腳本、逐份比對結果、待確認問題 | 否（`data/tmp/`） |
| 3. 核對範圍：雙方螢光標記 | 開發者與作業人員各畫一份，作業人員決定差異 | 兩份螢光 PDF、逐處對照表、範圍決定 | PDF 否（`data/`）；決定記錄於 Issue |
| 4. 規格文件 | 開發者 | 範本規格、詢價格式文件與設定、核對規則、審查標準差異 | 是（docs PR） |
| 5. 實作 Issue | 開發者 | 可驗收的 Issue；太大就分階段 | GitHub |
| 6. 實作與測試 | 開發者 | parser、規則、合成測試、本機真實樣本測試 | 是（實作 PR） |
| 7. 試跑 | 作業人員 | 新交易實跑紀錄、誤判回饋 | 回饋開 Issue |

第二家上手前，另需一次性的多上手架構工作（§7）。

## 1. 準備樣本

| 項目 | 建議數量 | 說明 |
|---|---|---|
| 說明書 PDF | ≥ 10 份 | 盡量涵蓋各型態：記憶式與否、KO 觀察方式、有無 KI 與 KI 型態、幣別、標的數、天期 |
| 原始詢價表 | ≥ 1 份，最好每種型態 1 份 | 上手寄來的原始格式，不要用整理過的彙總表 |
| SOP 特殊規定 | 有就提供 | 固定警語、名稱格式、受託機構審查日期等是否與既有上手不同 |
| 負面樣本 | 其他上手各 1 份 | 驗證範本辨識不會誤判 |

規則：

- 真實檔案只放本機被 Git 忽略的 `data/`（說明書與投資人須知 `data/term-sheets/`、參考條件表與詢價表 `data/orders/`、探勘輸出 `data/tmp/`；本機真實樣本測試讀的結構見 `tests/real_data.py`）。
- 檔名可能不規則（例如中間有空格）；配對一律用商品代號，不靠檔名。
- 樣本不足的型態要在規格中註明「依規則推得、待樣本驗證」，不可當成已驗證。

## 2. 探勘

目標：在寫正式程式前，確認版面、錨點、型態差異與每條規則都能在樣本上成立。

1. **文件特性**：文字層或掃描（掃描需另評估 OCR，見 TODO §3）、頁數、頁尾雜訊、字型、座標系。
2. **骨架與錨點**：章、條、子項的編號方式與標題；每個欄位用「章＋條號＋子項＋標籤」定位，**不可用頁碼**。
3. **型態維度**：列出所有變化（例：記憶式、KO 觀察方式、KI、標的數），每份樣本歸類成總表。
4. **表格**：表頭文字與 x 座標、列首、跨頁、換行註記、N/A 寫法。
5. **同一參數的多個出處**：主來源與交叉驗證出處。
6. **容易誤抓**：數字相同但意義不同、標籤帶符號、全半形混用、單一／多標的寫法差異。
7. **詢價表**：工作表、商品代號位置、表頭列、欄名、允許值、日期與百分比的儲存方式。
8. **逐份比對**：探勘腳本把說明書與詢價表逐項比對，所有差異都要能解釋（真實差異、進位規則、或尚未理解的語意）。
9. **待確認問題**：用白話整理給作業人員，例如容差、進位方式、哪些欄位不核對、詢價表欄位語意。每個決定記錄日期。

探勘腳本只是參考邏輯，正式程式依規格重寫並以測試保證，不直接沿用。

## 3. 核對範圍：雙方螢光標記（最大公因數）

目標：在寫規格前決定「這個範本要核對哪些地方」。開發者與作業人員各自在說明書上畫螢光，雙方都畫的部分（交集）是共同核心；差異逐項由作業人員決定，不自動擴增，也不自動刪除。流程源自 BARC 的事後補做（Issue #38）。

1. **選樣本**：每個範本至少 1 份代表樣本；型態差異會造成不同段落（例如有無 KI、KO 觀察方式不同）時，每種段落各加 1 份。
2. **開發者先畫**：依探勘結果，在 PDF 複本上把「建議核對」的每一處畫上螢光，另附清單（頁碼、標記原文、建議規則、比對基準）。須在看到作業人員的標記前完成並保存，不得事後修改。
3. **作業人員獨立畫**：在另一份複本上，畫出手工核對時實際會看的每一處，不參考開發者版本。
4. **逐處對照**：用 PyMuPDF 讀出兩份的 Highlight 註解，依頁碼與順序編號後逐處配對。配對單位是「欄位＋條款語意」，不是數字本身：
   - 同一數值出現在不同位置（例如正式條款與情境試算各出現一次）算不同處。
   - 整段標記只依其中有意義的欄位判斷，不宣稱整段文字都已核對。
   - 只核對一部分（例如只核對欄位、不核對每次重複出現、表頭或公式）記為「部分涵蓋」，不算交集。
5. **分類與決定**：

   | 類別 | 處理 |
   |---|---|
   | 雙方都畫（交集） | 共同核心，直接列為核對規則 |
   | 只有作業人員畫 | 逐項決定納入／暫緩／不檢查；納入時指定基準：PDF 內部一致、詢價表、審查標準固定值、重算，或外部來源 |
   | 只有開發者畫 | 逐項決定保留或移除；結構性檢查（日期表關係、格式與來源檢查、禁用語）預設保留 |
   | 部分涵蓋 | 確認要補的是欄位、每次出現、表頭、公式，還是外部數值的正確性 |

   決定時說明「沒有權威來源就無法真正核對」的項目，例如只有說明書本身出現的值；這類項目只能做文件內一致或列為不核對。
6. **記錄**：對照表與決定寫在該上手的探勘或範圍 Issue（只用標記編號、條號與欄位名，不含真實值）；決定同步到核對規則文件的「不核對項目」與「決策紀錄」。螢光 PDF 與含真實值的對照表只放 `data/`（建議 `data/tmp/<上手>_highlight_dev.pdf`、`data/tmp/<上手>_highlight_ops.pdf`）。
7. **回歸案例**：每條因此新增的規則，在實作時要有合成反例，特別是「正式條款正確、但重複出現處錯誤」的情況。

既有上手補做：BARC 以 Issue #38 補做（只有作業人員標記，與現有程式規則對照）；HSBC 在實作 Issue #34 開工前補做，結果補進 HSBC 核對規則。同一上手的新範本版本只需針對有變動的段落重畫。

## 4. 規格文件（docs PR）

前置條件：§3 的核對範圍已由作業人員確認。核對規則只寫入交集與確認納入的項目；確認不檢查的項目列入「不核對項目」。

| 文件 | 路徑 | 內容 |
|---|---|---|
| 範本規格 | `docs/templates/<上手>-<範本>.md` | 範本代號（例 `xxx-zh-pd`）、文件特性、骨架、欄位錨點表、型態維度、表格解析、多出處、誤抓清單、**範本辨識條件**、樣本總表 |
| 投資人須知範本規格 | `docs/templates/<上手>-zh-iis.md` | 每檔商品另有一份投資人須知（[ADR 0007](adr/0007-iis-paired-with-term-sheet.md)）：骨架、欄位錨點、範本辨識條件，並在[投資人須知核對規則](rules/iis-check-rules.md) §3 補該上手的檢查點欄 |
| 參考條件表對照 | `docs/order-formats/<上手>-fcn-reference.md` | 參考條件表各欄在該上手說明書的來源、比價日的日期定義；共用格式見 [reference-sheet.md](order-formats/reference-sheet.md) |
| 核對規則 | `docs/rules/<上手>-check-rules.md` | 標準欄位對照、值對應、數值與容差、推算規則、說明書內部交叉驗證、日期規則、不核對項目、待確認事項、決策紀錄 |
| 審查標準 | `config/review_standard.toml`＋`docs/rules/review-standard.md` | 只新增該上手不同的基準（例如名稱樣板）；共用基準不重複 |

撰寫規則：

- 範例數值一律虛構；樣本以代號（例 S01–S10）表示，代號與真實商品代號的對照只存在本機 `data/tmp/`。
- **標準欄位名稱沿用既有命名**（清單見 `src/fcn_checker/standard_fields.py`，例：`trade_date`、`strike_pct`、`ki_type`），讓通用規則可以共用；上手特有欄位才新增名稱。
- 每條規則寫清楚：主來源、比對方式、容差、抓不到或有歧義時的處理（一律轉人工覆核）。
- 「不適用」（例如無 KI）必須能由說明書明確判定，不能因抓不到而推定。

## 5. 實作 Issue

- 依 AGENTS.md 建立實作就緒的 Issue（繁體中文），驗收條件可觀察。
- 一個 PR 做不完就分階段，例如 BARC：第一階段主要條款與審查標準（#7），第二階段配息表與提前出場表（#9）。未做的規則列入報告「未涵蓋」清單，不得假裝通過。
- 樣本不足、無法確認寫法的型態（例如 BARC 的 Monthly KI）另開 Issue，先維持人工覆核。
- 測試切點事先約定：批量入口 `fcn_checker.batch` 與 `fcn-batch` CLI；不直接測擷取或解析的內部函式。

## 6. 實作與測試

### 6.1 程式位置

| 內容 | 路徑 | 說明 |
|---|---|---|
| 上手註冊 | `src/fcn_checker/issuers.py` | 在 `REGISTRY` 登記一筆 `Issuer`，只有五樣（§6.2a）：識別資料（`code`、`template_id`、`label`、`parser_version`、`not_covered`）、`detect`、`read`、`rules`、`reference_fields`（通常為空） |
| 上手編號 | `config/issuer_prefixes.toml`、`config/reference_sheet.toml` | 登記商品代號前三碼 → 上手代號，以及該上手在參考條件表「發行機構」欄的寫法（[參考條件表格式](order-formats/reference-sheet.md)） |
| 說明書 parser | `src/fcn_checker/parsers/<上手>.py`（表格可拆檔） | 範本辨識 `detect`；讀出 `read` 交出 `standard_fields.STANDARD_FIELDS` 的全部標準欄位（含提前出場排程、各出處清單）與該上手規則需要的專屬資料；`TEMPLATE_ID`、`PARSER_VERSION`；提供自己的 `LayoutSpec` |
| 投資人須知 parser | `src/fcn_checker/parsers/<上手>_iis.py` | `detect`、`read` 交出 `parsers/iis.py` 的 `IisSheet`（範本有的欄位以 `provides` 宣告），在 `issuers.py` 以 `IisTemplate` 掛到該上手的 `iis`；規則各上手共用（`rules/iis.py`、`review_standard.iis_review_standard_rules`），範本特有的固定警語次數放審查標準 `[iis]`（ADR 0007） |
| 版面工具 | `src/fcn_checker/parsers/layout.py` | 共用；章名、條號、子項格式由各上手的 `LayoutSpec` 提供，不複製一份 |
| 參考條件表 adapter | `src/fcn_checker/orders/reference.py` | 所有上手共用，欄名對應走設定檔；新上手不需新增 adapter |
| 規則 | `src/fcn_checker/rules/<上手>.py` | 只寫該上手專屬的說明書內部規則（不碰參考條件表）與未涵蓋清單；其他規則自動沿用（§6.2a） |
| 合成測試資料 | `tests/<上手>_synth.py` | 依該上手版面產生虛構 PDF，只用 `tests/pdf_writer.py` 排版；參考條件表用 `tests/reference_synth.py`，單份核對用 `tests/harness.py`；不引用其他上手合成器 |
| 測試 | `tests/test_check_<上手>*.py`、`tests/test_real_samples.py` | 合成測試進 CI；真實樣本測試只在本機 |

### 6.2a 新上手要提供什麼、哪些自動沿用（[ADR 0005](adr/0005-issuer-adapter-and-shared-rules.md)）

| 新上手提供 | 說明 |
|---|---|
| 識別資料 | 上手代號、範本 ID、名稱、parser 版本、未涵蓋清單 |
| `detect(lines)` | 是否為這家上手的範本，含證據；條件不成立的原因寫進 `failed` |
| `read(lines)` | 回傳實作 `standard_fields.TermSheet` 的物件：`f(name)` 交出全部標準欄位（缺漏、歧義、不合法、不適用分開表示），`full_text` 為全文索引（固定警語、風險等級、禁用語規則使用），另可帶該上手規則需要的專屬資料。提前出場排程（`autocall_schedule`）、最低金額／受理申購日／刊印日期的出處清單（`Occurrence`）也在這裡推好。`f` 不丟例外：沒交出的欄位回傳 `standard_fields.not_provided(name)`，可直接用 `standard_fields.lookup(欄位字典, name)` 實作；範本本身沒有的欄位（例：MS 沒有年利率、受理申購日）要明確交出 `standard_fields.absent(name, 說明)`（不適用），共用規則不核對、不報缺漏 |
| `rules(ctx)` | 該上手專屬的說明書內部規則（例：BARC §13／§16、HSBC §18 情境與日期表結構；價格推算已是共用規則，不必再寫）；`ctx` 是 `rules/kit.py` 的 `IssuerContext`，只有讀出結果、審查標準與上手代號 |
| `reference_fields` | 說明書內部規則必須讀參考條件表時才宣告的欄位（ADR 0005 的例外，需經審查才加；BARC 為年利率與天期、MS 為年利率）；讀未宣告的欄位是開發期錯誤 |

自動沿用（不必再寫）：

- 參考條件表欄位規則（`rules/reference.py`）：商品代號、承作幣別、UL 與各標的價格、百分比、天期、日期、單位面額、最低金額、KO／KI 欄位、Non-Call。
- 說明書推算規則（`rules/derivation.py`）：價格推算 `derive.prices`（各標的執行價／KO 價／下限價 = 最初價格 × 百分比，四捨五入到 4 位；價格表列數 = 標的數、下限價欄與 KI 型態一致），只讀 `underlyings`、`underlying_prices`、`strike_pct`／`ko_pct`／`ki_pct`、`ki_type`。
- 審查標準規則（`rules/review_standard.py` 的 `review_standard_rules`）：面額預設值、受理申購日、刊印日期、審查日期、負責人、固定警語、風險等級、禁用語、商品名稱、發行機構全名、受託機構資訊、費率、發行價格。上手不同的基準只放在 `config/review_standard.toml`。
- 回填（`backfill.py`）：TS、IIS 打勾，ISIN、發行日、比價日的核對、回填決策與寫入。
- 單份核對順序（`single_check.py`）與批量入口、CLI、PANEL。

標準欄位清單（`standard_fields.STANDARD_FIELDS`）就是共用規則會讀的全部說明書欄位，分兩組：

- 參考條件表欄位與回填用：商品代號 `product_code`、`isin`、中文幣別 `currency_zh`、標的 `underlyings`、各標的價格列 `underlying_prices`、百分比 `strike_pct`／`ko_pct`／`ki_pct`／`coupon_pa_pct`、天期 `tenor_months`、日期 `trade_date`／`issue_date`／`final_valuation_date`／`maturity_date`、面額 `denomination`、KO 觀察方式 `ko_observation`、記憶式 `ko_memory`、KI 型態 `ki_type`、提前出場排程 `autocall_schedule`、出處清單 `min_amounts`／`subscription_dates`／`print_dates`。
- 審查標準規則用：商品中英文名稱 `name_zh`／`name_en`、審查通過日期 `approval_date`、負責人姓名 `chairman`、發行價格 `issue_price_pct`、發行機構名稱 `issuer_name_cover`／`issuer_name_ch2`／`issuer_name_ch1`（只寫中文的出處，範本沒有就交出 `absent`）、受託或銷售機構 `distributor_name_cover`／`distributor_phone_cover`／`distributor_address_cover`／`distributor_name_ch2`／`distributor_address_ch2`，以及費用表各項費率（名稱由 `standard_fields.fee_field(<費用項目>)` 產生，費用項目同 `config/review_standard.toml` 的 `[fees]`）。

各欄位值的形狀見 `STANDARD_FIELDS` 與 `FEE_FIELD_SHAPE`。共用規則只透過 `rules/kit.py` 的 `read_standard` 讀欄位，讀不在清單上的名稱會直接失敗；新增共用規則需要的欄位時，同步更新 `STANDARD_FIELDS`、本節與各上手的 `read`。adapter 少交某個標準欄位時，相關規則轉人工覆核並寫出欄位名稱，不會整份變成執行錯誤；兩家上手語意不同時以 BARC 為準。

### 6.2 實作原則

- 規則式、不使用 LLM（[ADR 0001](adr/0001-deterministic-runtime.md)）；PDF 擷取沿用 PyMuPDF（[ADR 0002](adr/0002-python-cli-pymupdf.md)）。新增依賴或改擷取方式需另立 ADR。
- 每個欄位帶狀態：present／missing／ambiguous／invalid／not_applicable，並保留證據（頁碼、bbox、原文）。
- 每條規則有 `rule_id`，只接收標準化欄位、下單欄位與審查標準；不讀檔、不改來源值。
- 會隨時間改變的基準放設定檔，不寫死在程式。
- 數值一律 Decimal；Excel 值先四捨五入到 9 位清除浮點尾數；日期不猜日月順序。

### 6.3 測試

- **合成測試**（CI）：每種型態至少一個全部通過的案例；每條規則至少一個 PASS 與一個 MISMATCH／REVIEW 案例；另含範本辨識失敗、欄位缺漏、歧義、未知參考條件表欄名或欄位值、跨頁表格、損毀或加密 PDF。
- **本機真實樣本測試**：只在 `data/` 存在時執行，CI 自動略過；斷言與探勘結論一致（例如「只有舊文件的審查日期不符」）。測試碼不得含真實代號或數值。
- **負面測試**：其他上手的樣本不得被判定為本範本，既有上手的樣本也不得被新範本誤判。

### 6.4 驗收門檻（合併前）

- [ ] 全部樣本都能辨識為正確範本，負面樣本全部拒絕。
- [ ] 參考條件表有列的樣本：已涵蓋規則全部通過，或差異經作業人員確認為真實差異。
- [ ] 全部樣本的說明書內部規則結果與探勘一致。
- [ ] `pytest`、`ruff check`、`ruff format --check` 通過；CI 綠燈。
- [ ] 已跑 code review 並處理發現。
- [ ] 文件同步：範本規格狀態、核對規則的實作對照、README、TODO。

## 7. 第二家上手：一次性的多上手架構工作

**已完成（Issue #28）**。下表保留為紀錄；新上手只需依 §6.1 新增 parser、規則、設定並在 `issuers.py` 登記。

| 位置 | 原本 | 調整結果 |
|---|---|---|
| `checker.py` | 直接呼叫 BARC parser 與規則，`template.barc` | 依序以各上手 `detect` 辨識；恰好一個命中才繼續，零個（`template_unknown`）或多個（`template_ambiguous`）命中轉人工覆核；`rule_id` 為 `template.detect`；詢價格式設定的上手不符 → `order.issuer` 人工覆核 |
| `cli.py` | 預設 `config/order_formats/barc.toml` | `--order-format` 選填；未指定時依辨識到的上手選格式設定 |
| `panel.py`、`panel_workflow.py` | 只列 BARC 選項並呼叫 BARC `detect` | 由上手註冊表產生選項；格式設定依選取的上手（雙擊入口傳 `--order-formats-dir`） |
| `config.py`、`review_standard.toml` | `product_name.barc` 寫死 | 依上手讀取 `product_name.<上手>`；其他依上手不同的基準日後同樣分節，共用基準不變 |
| `rules/barc.py` | 通用規則與 BARC 專屬規則放在一起 | 通用部分（結果建構、缺值處理、百分比與日期比對、詢價表欄位檢查、審查標準規則）抽到 `rules/common.py`，各上手只寫專屬規則與「未涵蓋」清單 |
| `parsers/layout.py` | 章名、條號格式依 BARC | `LayoutSpec` 參數，由各上手提供（BARC：`parsers/barc.py` 的 `LAYOUT`） |

架構調整要維持既有 BARC 測試與本機真實樣本測試全部通過（行為不變）。

## 8. 試跑與維護

- 正式使用前，用未參與開發的新交易實跑一段時間，記錄誤判與人工覆核比例（TODO §2）。
- 誤判依 AGENTS.md 的 Bug 流程處理：原因清楚 → Issue → 修正並加回歸測試；原因不清楚 → 先診斷。
- 上手改版（新欄位、新寫法）時，工具應轉人工覆核而不是靜默通過；確認新寫法後更新規格與 `PARSER_VERSION`。
- 審查標準變動（例如受託機構重新審查）只改設定檔，並更新 `version`、`effective_date`。

## 9. 資料安全

- 真實說明書、螢光標記 PDF、詢價表、擷取文字與報告只放被 Git 忽略的 `data/`、`runtime/`。
- Issue、PR、文件、測試與 CI 不得含真實商品代號、ISIN、價格、報價編號或客戶資料。
- Repo 若改為公開，以上規定更須嚴格遵守；提交前一律檢查 diff。

## 10. 檢查清單

- [ ] 樣本放入 `data/`，涵蓋主要型態；負面樣本齊全
- [ ] 探勘完成，所有比對差異都有解釋；待確認問題已由作業人員回覆並記錄日期
- [ ] 開發者與作業人員各自完成螢光標記（開發者版本先完成）；交集與差異的決定已記錄
- [ ] 範本規格、詢價格式文件與設定、核對規則、審查標準差異已合併
- [ ] 實作 Issue 建立；必要時分階段；樣本不足的型態另開 Issue
- [ ] 已在 `issuers.py` 登記新上手；既有上手的測試與本機真實樣本測試不變
- [ ] parser、規則、合成測試、本機真實樣本測試完成；驗收門檻全部達成
- [ ] 文件同步並合併
- [ ] 試跑並回饋
