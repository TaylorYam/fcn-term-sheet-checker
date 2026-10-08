# FCN Term Sheet Checker

FCN Term Sheet 自動核對專案：將條款文件與已確認的下單資料轉成相同資料結構，以可追溯規則產生差異與人工覆核清單。

**目前狀態：可用——BARC／HSBC 中文產品說明書（文字型 PDF）× 參考條件表的本機批量核對（PANEL 與 CLI），並回填 ISIN 與比價日**，含主要條款、配息表與提前出場表、保證配息期、日期規則、審查標準，以及文件內重複出現處與情境試算的一致性。尚未涵蓋的 Monthly KI、標的名稱、最初價格外部正確性與部分情境試算會列在 PANEL 的「待處理」分頁與核對紀錄的「未涵蓋」清單。第一版 production runtime 不使用 LLM；LLM 可協助開發，但不參與正式擷取或判定。

## 運作說明

**用途**：把上手給的中文產品說明書 PDF，跟作業人員事先填好的參考條件表逐欄對帳。整份對得上，就把 ISIN、發行日、比價日自動填回參考條件表；對不上，就列出哪一項對不起來、兩邊各寫多少。

```text
說明書 PDF（可多份）──┐
                     ├→ 用商品代號配對 → 逐項核對 → 整份通過：回填 ISIN、發行日、比價日
參考條件表 Excel ─────┘                          └→ 有問題：列入錯誤清單（人工看過可放行）
```

**程式怎麼找到每個參數**

- **先分上手、再確認版本**：檔名前三碼決定上手（029 巴克萊、325 滙豐），每家各有一套讀法。讀之前先確認這份 PDF 是程式認得的版本（封面標題、發行機構、商品名稱、章節順序都要符合）；上手改版或格式不符時，整份轉人工。
- **照「地址」找，不靠頁碼**：同一條款在不同份 PDF 會差 1～2 頁，但章節骨架固定，所以照「章 → 條 → 子項 → 標籤」定位。例如發行日在「第一章第 13 條第 (3) 項『發行日：』」，執行 % 在第 15 條「執行價格」那段。
- **表格依位置拆欄拆列**：依表頭的水平位置分欄、依高度相近分列，跨頁也能接起來。配息表、提前出場表、價格表、標的清單都這樣讀。
- **商品型態從內容判斷，不預設**：記憶式與否、提前出場每日或定日觀察、從第幾期開始可提前出場、有沒有 KI、標的數，都由文件內容判斷。例如「沒有 KI」必須確認文件真的沒有那段定義才算，不會因為抓不到就當成沒有。

**檢查哪些東西**

| 類別 | 內容 |
|---|---|
| 跟參考條件表比 | 幣別、面額、標的與各標的進場價／執行價／下限價／KO 價（`期初定價` 為 `VWAP` 時不比，改以說明書回填）、執行 %、KO %、KI %、提前出場觀察方式、記憶式、KI 型態、年利率、天期、交易日／發行日／最終比價日／到期日、Non-Call |
| 說明書自己前後一致 | 同一數字出現在多處要相同；價格 = 最初價格 × 百分比；配息表、提前出場表日期與情境試算算得通 |
| 公司規定（審查標準） | 受託機構名稱／電話／地址、負責人姓名字碼、固定警語全文與出現次數、風險等級、禁用詞、費率、商品名稱格式、面額。規定改變時只改設定檔，不改程式 |

**判定原則**

- **不猜值**：每一項是「通過」「不一致」或「需人工覆核」。抓不到、同一欄出現不同的值、沒見過的寫法，一律轉人工；整份全部通過才回填。
- **人工放行**：作業人員看過錯訊認定可以通過時，可以手動放行；但回填值不確定或和參考條件表已有的值衝突時不能放行。
- **不使用 AI**：全部是固定規則，同樣的檔案每次結果相同。
- **可追溯**：每個值都帶說明書頁碼與原文；每次儲存另留核對紀錄（程式版本、檔案指紋、逐項結果），事後可查。
- **資料不外流**：只在本機執行、不連網、沒有伺服器，原檔不會被修改。

**目前範圍與限制**

- 支援巴克萊、滙豐的中文產品說明書（文字型 PDF），讀法依 14 份巴克萊、8 份滙豐真實說明書建立並驗證。
- 不核對、列在「待處理」提醒人工看的項目：Monthly KI（尚無樣本）、標的中文名稱、最初價格本身是否正確（沒有外部資料可比）、部分情境試算。
- 新上手或上手改版：先轉人工，取得樣本後再擴充讀法，流程見[新增上手實作規範](docs/issuer-onboarding.md)。

## 預定流程

```text
PDF → 逐頁文字擷取／必要時 OCR → 已知範本 parser → 標準化條款
                                                             ↓
已確認下單資料 → 欄位映射與驗證 → 標準化預期條款 → 規則核對 → 例外報告
```

- 文字型 PDF：優先評估 PyMuPDF / pdfplumber，保留頁碼與座標。
- 掃描或混合型 PDF：只有需要的頁面走本地 OCR；OCR 不等於 LLM。
- 未知範本、欄位缺漏、值衝突、OCR 不可靠：進人工覆核，不猜值、不自動通過。
- 未來 LLM fallback 僅記錄擴充邊界；第一版不安裝 SDK、不設定金鑰、不呼叫模型。

## 目前進度（2026-10-01）

- 第一個 issuer：巴克萊（BARC）中文產品說明書。14 份真實樣本皆為文字型 PDF，已解構版面、錨點與 4 個變化維度：[BARC 範本規格](docs/templates/barc-zh-product-description.md)。
- 核對條件來源（2026-10-02 起）：所有上手共用的參考條件表（`FCN參考條件` 的 `樣本清單`）。PANEL 與 `fcn-batch` 批量核對並回填 ISIN 與比價日：[參考條件表格式](docs/order-formats/reference-sheet.md)、[核對規則](docs/rules/barc-check-rules.md)、[ADR 0004](docs/adr/0004-reference-sheet-as-check-source.md)。BARC 詢價表流程已刪除（Issue #46）。
- 第二家上手（已實作 Issue #34）：滙豐（HSBC）中文產品說明書，8 份文字型 PDF，已完成探勘與規格：[HSBC 範本規格](docs/templates/hsbc-zh-product-description.md)、[下單資料格式](docs/order-formats/hsbc-fcn-reference.md)、[核對規則](docs/rules/hsbc-check-rules.md)。H02 為唯一作業螢光樣本；其他樣本驗證不同型態，已支援參考條件表、價格／日期及情境簡單算式核對。
- 本機探勘：BARC 詢價表樣本與說明書 41 項全部一致；14 份說明書的 PDF 內部規則全部成立；文件資訊、日期規則與[審查標準](docs/rules/review-standard.md)已確認；標的目前只核對英文代號，中文名稱核對擱置（核對規則 §6.2）。

## 安裝

需要 Python ≥ 3.11。PDF 擷取使用 PyMuPDF（AGPL-3.0；本工具僅限公司內部本機使用），Excel 讀取使用 openpyxl；已驗證版本鎖定在 `constraints.txt`。

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

## 使用方式

### 批量核對與回填（參考條件表）

在專案根目錄執行（設定檔預設讀 `config/`）：

```bash
fcn-batch data/FCN參考條件_1001.xlsx data/ts/029*.pdf --out runtime/reports
```

| 參數 | 說明 |
|---|---|
| 第 1 個 | 參考條件表 Excel（讀 `樣本清單` 工作表），格式見[參考條件表格式](docs/order-formats/reference-sheet.md) |
| 其後 | 一或多份說明書 PDF；檔名前三碼是上手編號（`config/issuer_prefixes.toml`），用來挑範本 |
| `--review-standard` | 審查標準設定檔，預設 `config/review_standard.toml` |
| `--reference-format` | 參考條件表格式設定檔，預設 `config/reference_sheet.toml` |
| `--issuer-prefixes` | 上手編號對照設定檔，預設 `config/issuer_prefixes.toml` |
| `--out` | 核對結果檔的輸出資料夾，預設 `runtime/reports` |

- 每份說明書用商品代號對參考條件表上的 `TDCC Code`，拿那一列的條件來核對。
- 結果是一份核對結果檔 `<原檔名>_核對結果_<日期時間>.xlsx`，存到 `--out` 資料夾，完成後印出檔案路徑。原檔不動，也不覆蓋既有檔案。
  - `回填後`：版面照原 `樣本清單`，只留整份 PASS 或在 PANEL 人工放行的列，空白的 `ISIN Code`、`發行日`、`比價日_1～12` 已回填；`期初定價` 為 `VWAP` 的列，各標的價格欄一律以說明書覆寫。可以直接匯入資料庫。
  - `錯誤清單`：每份沒通過（也沒有人工放行）的 PDF 一列（`TDCC Code`、`PDF 檔名`、`錯訊`）。錯訊是中文，只寫哪裡對不起來、兩邊各是多少，例如「UL_2 進場價對不起來：參考條件表 123.4500／說明書 123.4000」；多條在同一格換行。
- 上手編號不在對照表、或上手還沒有範本時，標示「未支援上手」。
- 結束碼同下表：未支援上手算 1；任一份 ERROR 或整批錯誤算 2。

`--out` 資料夾裡只有核對結果檔，不再有每份說明書的 JSON／Markdown 報告。另外每次儲存都會在執行目錄的 `runtime/核對紀錄/` 寫一份內部核對紀錄 `<日期時間>.json`（時間戳與核對結果檔相同，完成後印出路徑），供維護人員事後追查：程式版本與 commit、設定檔與審查標準的路徑和 hash、參考條件表與每份 PDF 的 hash，以及每份 PDF 的整體狀態、逐項結果（參考條件表值、說明書值、說明書頁碼與原文、參考條件表儲存格位置）與回填決策。同名紀錄不覆蓋；紀錄寫不進去時核對結果檔照樣產生，但會回報錯誤（結束碼 2）。

| 結束碼 | 整體狀態 |
|---|---|
| 0 | `PASS`：已涵蓋的規則全部通過（未涵蓋規則仍須人工核對） |
| 1 | 有 `MISMATCH`（不一致）或 `REVIEW_REQUIRED`（需人工覆核，含未支援上手） |
| 2 | `ERROR`：PDF 損毀／加密、檔案不存在、設定檔錯誤、寫檔失敗等 |

整體狀態優先順序 `ERROR > REVIEW_REQUIRED > MISMATCH > PASS`。抓不到的欄位、多個不同值、非已支援範本、參考條件表未知欄名或欄位值一律轉人工覆核，不猜值。

## 本機 PANEL：預覽、核對與保存

Windows 第一次使用：安裝官方 Python 3.11 以上（包含 Tcl/Tk），將專案放到自己有寫入權限的資料夾，雙擊 `setup_panel.cmd`。安裝會建立專案自己的 `.venv`，依 `constraints.txt` 安裝套件；需要可存取公司允許的 Python 套件來源。看到「安裝完成」後，雙擊桌面或專案資料夾裡的「Term Sheet 核對」捷徑開啟程式（安裝時自動建立；捷徑建立失敗時改雙擊 `launch_panel.cmd`）。日常操作不需輸入命令，不需要伺服器。搬移資料夾或更新程式後請重新執行安裝；公司若禁止 PowerShell 腳本，請由 IT 依公司政策協助安裝。

PANEL 使用 Python 內建 Tkinter，與 `fcn-batch` 共用同一套批量核對與回填流程。`launch_panel.pyw` 與 `fcn-panel` 入口仍可使用。

1. 選取一份參考條件表（`FCN參考條件` Excel），再選取一或多份說明書 PDF（可多選）。上手由 PDF 檔名前三碼決定，不需要另外選擇。兩個「選取檔案…」各自記得上次選檔的資料夾，下次直接從那裡開始。
2. 按「載入／重新載入預覽」：每份 PDF 一列，顯示對應的上手（或「未支援上手」）、PDF 第一頁的商品代號（直接讀取文字層，保留前導零）、對到參考條件表的第幾列，或找不到／多列／發行機構不符／同一批多份說明書對到同一列等原因。參考條件表的欄名問題另外列出。
3. 確認預覽後按「開始核對」，畫面切到「核對結果」：左側列出每份說明書（有問題的排前面），狀態用中文顯示；跟參考條件表配對不起來時直接寫原因，例如「條件表找不到這筆」「條件表有重複列」「多份對到同一列」「條件表發行機構不符」「檔名上手編號不符」。右側是選取那份的逐欄結果。選取結果列可查看原因（與核對結果檔「錯誤清單」同一段中文錯訊）、雙方值、參考條件表儲存格、PDF 頁碼及完整原文；下方列出這份的回填欄位（ISIN、發行日、比價日）及處理方式。
   - **人工放行**：作業人員看過錯訊，認定一份「不一致」或「需人工覆核」的說明書可以通過時，選取它後按下方「人工放行…」，確認視窗列出這份全部錯訊，確認後視同通過：儲存時回填、進「回填後」，不列入「錯誤清單」；狀態顯示「人工放行（原：不一致）」，摘要另計「X 份人工放行」；放行後清單改選最上面那份（下一份要處理的說明書）。同一個按鈕可「取消放行」，取消後仍選那份。回填值無法確定、參考條件表回填欄位已有不同的值、同一批多份對到同一列、找不到列、未支援上手或執行錯誤時不能放行，按鈕旁顯示原因。已儲存後再放行或取消放行，上一次的核對結果檔就不是目前的結果，要再按一次儲存。重新載入或重新核對會清除放行；CLI 沒有人工放行。
4. 「待處理」分頁列出未涵蓋的項目（Monthly KI、標的名稱等），不算成通過。
5. 預覽與結果只能查看（人工放行除外，它不改任何檔案，儲存時才生效）。更換來源會清除舊預覽及結果；外部修改或刪除參考條件表、任一 PDF、審查標準或設定檔時，畫面偵測後要求重新載入。讀檔與核對在背景執行，避免重複提交。
6. 按「儲存核對結果…」，選擇資料夾：核對結果檔 `<原檔名>_核對結果_<日期時間>.xlsx`（「回填後」＋「錯誤清單」）寫到該資料夾，完成訊息列出核對結果檔路徑。原參考條件表不動。內部核對紀錄自動寫到安裝根目錄的 `runtime/核對紀錄/`（更新程式時保留）；紀錄寫不進去時，完成訊息會列出原因。未按儲存不產生任何檔案；檔名已存在時不覆蓋，寫入失敗時列出原因，核對結果仍可查看與重試。

設定檔：PANEL 讀專案根目錄 `config/` 的 `review_standard.toml`、`reference_sheet.toml` 與 `issuer_prefixes.toml`。畫面上方會顯示實際使用的設定檔路徑。

**審查通過日期**：受託或銷售機構重新審查後，按設定檔路徑旁的「審查通過日期…」，輸入新的日期（YYYY-MM-DD）按「新增」；新日期須晚於目前最新的一筆，可以先輸入未來的日期。打錯時只能修改或刪除最新一筆，較早的日期不能改。每份說明書依交易日核對「當天或之前最近一次」的審查通過日期，所以舊說明書重新核對時仍以當時的日期為準。存檔時自動把 `review_standard.toml` 的 `version` 加 1、`effective_date` 改為當天，其餘內容與註解不動，並清除已載入的預覽與結果。改完請把設定提交到 main（例如請 Claude 開 PR），避免之後 `git pull` 時衝突。固定風險警語、商品名稱樣板等其他審查標準不在 PANEL 修改，改版時直接修改設定檔。

## 更新程式

PANEL 只有維護者本人使用。更新方式：在專案資料夾執行 `git pull`，再重新雙擊 `setup_panel.cmd`。安裝會把程式複製進 `.venv`，只 `git pull` 不會生效。根目錄的 PDF、Excel、核對紀錄（`runtime/`）及 `config` 不受影響；新版的內建設定若有變更，需自行比較後導入根目錄 `config`。

舊版曾提供「更新 GitHub 最新版」按鈕（[ADR 0003](docs/adr/0003-public-github-panel-update.md)，已由 [ADR 0006](docs/adr/0006-panel-maintainer-only-no-self-update.md) 取代）。它留下的 `.local/releases/`、`.local/current.json`、`.local/update.lock` 已不再使用，可以手動刪除；`.local/installed.json` 是安裝時記錄的版本，請保留。

## 開發與測試

```bash
pytest -q
```

```bash
ruff check src tests
```

測試只透過公開切點驗證：批量入口 `fcn_checker.batch`（`preview_batch`／`check_batch`）與儲存 `fcn_checker.saving`（`save_batch`／`run_batch`，回傳儲存收據；設定由核對設定 `fcn_checker.check_config.CheckConfig` 傳入）、`fcn-batch` CLI 與 PANEL 工作階段 `fcn_checker.panel_workflow.PanelSession`。合成說明書 PDF 由各上手的合成器（`tests/synth.py`、`tests/hsbc_synth.py`）產生，合成參考條件表與單份核對 harness（含載入一次的核對設定 fixture `CONFIG`）在 `tests/reference_synth.py`、`tests/harness.py`，數值皆虛構。`tests/test_real_samples*.py` 只在本機 `data/` 有真實樣本時執行，CI 自動略過。

## 文件與開發規則

- [架構與模組邊界](docs/architecture.md)
- [資料契約草案](docs/data-contract.md)
- [分階段 TODO 與待確認項目](docs/TODO.md)
- [新增上手（issuer）實作規範](docs/issuer-onboarding.md)
- [名詞表](CONTEXT.md)、[參考條件表格式](docs/order-formats/reference-sheet.md)（設定檔 `config/reference_sheet.toml`、`config/issuer_prefixes.toml`）
- [BARC 範本規格](docs/templates/barc-zh-product-description.md)、[BARC 詢價格式（已刪除，僅供回溯）](docs/order-formats/barc-inquiry.md)、[BARC 核對規則](docs/rules/barc-check-rules.md)、[審查標準](docs/rules/review-standard.md)（設定檔 `config/review_standard.toml`）
- 投資人須知（IIS，待實作）：[核對規則](docs/rules/iis-check-rules.md)、[BARC 範本規格](docs/templates/barc-zh-iis.md)、[HSBC 範本規格](docs/templates/hsbc-zh-iis.md)
- ADR：[0001 第一版採規則式核對](docs/adr/0001-deterministic-runtime.md)、[0002 本機 Python CLI／PyMuPDF](docs/adr/0002-python-cli-pymupdf.md)、[0003 PANEL 以公開 GitHub main 更新（已取代）](docs/adr/0003-public-github-panel-update.md)、[0004 核對條件統一改用參考條件表](docs/adr/0004-reference-sheet-as-check-source.md)、[0006 PANEL 只供維護者使用，移除自動更新](docs/adr/0006-panel-maintainer-only-no-self-update.md)、[0007 投資人須知與說明書成對核對](docs/adr/0007-iis-paired-with-term-sheet.md)
- [AGENTS.md](AGENTS.md)：共用開發規範；[CLAUDE.md](CLAUDE.md) 沿用此規範。
- `.github/ISSUE_TEMPLATE/`、PR 範本、CI 皆保留自原始 template。

開發流程：Issue → branch/worktree → plan → implementation → validation → commit/push → PR → review/CI → merge。CI 檢查 patch 空白、ruff lint／format 與 pytest（Python 3.11、3.13，只用合成資料）。

## 資料管理

真實 PDF、下單檔、擷取文字、OCR 影像、核對結果檔與核對紀錄放在被 Git 忽略的 `data/` 或 `runtime/`。目前慣例：TS 放 `data/ts/`、下單 Excel 放 `data/`、探勘輸出與暫存檔放 `data/tmp/`、CLI 核對結果檔預設輸出到 `runtime/reports/`、核對紀錄在 `runtime/核對紀錄/`。不要把客戶資料、交易細節或憑證貼到公開文件、Issue、PR、測試快照及 CI artifact。資料保存期限與存取權限於導入前確認。

## 範本來源

由 [TaylorYam/ai-project-template](https://github.com/TaylorYam/ai-project-template) 使用 GitHub Template 建立，沿用私人可見性。初始化需求見 [Issue #1](https://github.com/TaylorYam/fcn-term-sheet-checker/issues/1)。
