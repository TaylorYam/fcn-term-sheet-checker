# FCN Term Sheet Checker

FCN Term Sheet 自動核對專案：將條款文件與已確認的下單資料轉成相同資料結構，以可追溯規則產生差異與人工覆核清單。

**目前狀態：可用——BARC／HSBC 中文產品說明書（文字型 PDF）× 參考條件表的本機批量核對（PANEL 與 CLI），並回填 ISIN 與比價日**，含主要條款、配息表與提前出場表、保證配息期、日期規則、審查標準，以及文件內重複出現處與情境試算的一致性。尚未涵蓋的 Monthly KI、標的名稱、最初價格外部正確性與部分情境試算會列在報告的「未涵蓋」區。第一版 production runtime 不使用 LLM；LLM 可協助開發，但不參與正式擷取或判定。

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
| `--out` | 每份說明書報告的輸出資料夾，預設 `runtime/reports` |

- 每份說明書用商品代號對參考條件表上的 `TDCC Code`，拿那一列的條件來核對。
- 整份 PASS 的說明書，會把空白的 `ISIN Code`、`發行日`、`比價日_1～12` 回填到新檔 `<原檔名>_回填_<日期時間>.xlsx`，跟原檔放在同一個資料夾。原檔不動，也不覆蓋既有檔案。
- 新檔多一張 `核對結果` 工作表，列出每份 PDF 的狀態與問題摘要。
- 上手編號不在對照表、或上手還沒有範本時，標示「未支援上手」。
- 結束碼同下表：未支援上手算 1；任一份 ERROR 或整批錯誤算 2。

每份說明書輸出 `<PDF 檔名>_<日期時間>.check.json`（完整逐項結果、證據、回填決策與執行 metadata）與 `.check.md`（人看的報告：先列不一致與需人工覆核項目，再列回填欄位、未涵蓋規則與通過項目）；檔名已存在時不覆蓋。每項結果附參考條件表值、說明書值、說明書頁碼與原文、參考條件表儲存格位置。

| 結束碼 | 整體狀態 |
|---|---|
| 0 | `PASS`：已涵蓋的規則全部通過（未涵蓋規則仍須人工核對） |
| 1 | 有 `MISMATCH`（不一致）或 `REVIEW_REQUIRED`（需人工覆核，含未支援上手） |
| 2 | `ERROR`：PDF 損毀／加密、檔案不存在、設定檔錯誤、寫檔失敗等 |

整體狀態優先順序 `ERROR > REVIEW_REQUIRED > MISMATCH > PASS`。抓不到的欄位、多個不同值、非已支援範本、參考條件表未知欄名或欄位值一律轉人工覆核，不猜值。

## 本機 PANEL：預覽、核對與保存

Windows 第一次使用：安裝官方 Python 3.11 以上（包含 Tcl/Tk），將專案放到自己有寫入權限的資料夾，雙擊 `setup_panel.cmd`。安裝會建立專案自己的 `.venv`，依 `constraints.txt` 安裝套件；需要可存取公司允許的 Python 套件來源。看到「安裝完成」後，雙擊 `launch_panel.cmd` 開啟程式。日常操作不需輸入命令；每位同事各自安裝、核對、保存，不需要伺服器。搬移資料夾或更新程式後請重新執行安裝；公司若禁止 PowerShell 腳本，請由 IT 依公司政策協助安裝。

PANEL 使用 Python 內建 Tkinter，與 `fcn-batch` 共用同一套批量核對與回填流程。`launch_panel.pyw` 與 `fcn-panel` 入口仍可使用。

1. 選取一份參考條件表（`FCN參考條件` Excel），再選取一或多份說明書 PDF（可多選）。上手由 PDF 檔名前三碼決定，不需要另外選擇。
2. 按「載入／重新載入預覽」：每份 PDF 一列，顯示對應的上手（或「未支援上手」）、PDF 第一頁的商品代號（直接讀取文字層，保留前導零）、對到參考條件表的第幾列，或找不到／多列／發行機構不符／同一批多份說明書對到同一列等原因。參考條件表的欄名問題另外列出。
3. 確認預覽後按「開始核對」，畫面切到「核對結果」：左側列出每份說明書（有問題的排前面），右側是選取那份的逐欄結果。選取結果列可查看原因、雙方值、參考條件表儲存格、PDF 頁碼及完整原文；下方列出這份的回填欄位（ISIN、發行日、比價日）及處理方式。
4. 「待處理」分頁列出未涵蓋的項目（Monthly KI、標的名稱等），不算成通過。
5. 預覽與結果只能查看。更換來源會清除舊預覽及結果；外部修改或刪除參考條件表、任一 PDF、審查標準或設定檔時，畫面偵測後要求重新載入。讀檔與核對在背景執行，避免重複提交。
6. 按「儲存報告與回填新檔…」，選擇報告資料夾：每份說明書的 JSON／Markdown 報告寫到該資料夾，整份通過的說明書回填到參考條件表旁的新檔 `<原檔名>_回填_<日期時間>.xlsx`（原檔不動，新增「核對結果」工作表）。未按儲存不產生任何檔案；檔名已存在時不覆蓋，部分失敗時逐份列出原因，核對結果仍可查看與重試。

設定檔：PANEL 讀根目錄 `config/` 的 `review_standard.toml`；`reference_sheet.toml` 與 `issuer_prefixes.toml` 若根目錄沒有（例如更新前安裝的環境），改用程式內建的同名設定。畫面上方會顯示實際使用的設定檔路徑。

## PANEL 更新 GitHub 最新版

安裝本次版本後，PANEL 提供「更新 GitHub 最新版」按鈕。第一次導入更新按鈕需取得本次程式並重新雙擊 `setup_panel.cmd` 安裝；之後從 `launch_panel.cmd` 開啟即可更新，不需 Git 或 GitHub 登入。

1. 按鈕會背景檢查固定 Repo `TaylorYam/fcn-term-sheet-checker` 的 `main`，顯示目前與最新 commit；同版本不重裝。首次 ZIP 安裝沒有版本紀錄時會顯示未知。
2. 有新版時先保存核對報告，再確認更新；取消保留畫面。更新期間不能重複提交、核對、保存或關閉視窗。
3. 新版下載到 `.local/releases/`，使用獨立 venv 安裝固定套件，驗證成功後才切換版本並重新啟動。失敗會顯示原因並保留舊版與當次結果；重啟成功後重新選檔與核對。
4. 根目錄的 PDF、Excel、報告及 `config` 保留。更新程式與套件，**不自動更新本機審查設定**；新版內建設定留在新版本資料夾供維護人員比較後導入。更新不會上傳交易資料。

Repo 須先公開，且電腦能存取 GitHub／公司允許的 Python 套件來源；私有、404、離線、逾時或限流都會顯示原因。程式不保存 Token，也不會替你公開 Repo。從 `fcn-panel` 或 module 直接啟動若沒有安裝根目錄，會提示改用雙擊入口。

舊版與未完成安裝資料夾不自動刪除。更新非正常中斷可能留下 `.local/update.lock`；請由維護人員確認相關程序已停止後處理。要回到根目錄版本，可關閉所有 PANEL 再執行 `setup_panel.cmd`，成功後會重設啟動版本。搬移整個安裝資料夾後仍建議重新安裝。

驗收：合成 HTTP／ZIP 與程序邊界測試涵蓋版本、不可存取、安全解壓、安裝／切換／重啟失敗；Windows 隔離部署驗證真實 venv、安裝與 GUI 重啟。Repo 目前仍私有，匿名 GitHub 成功下載的端到端驗收須公開後再執行；其他同事電腦與公司網路政策仍需試裝。

## 開發與測試

```bash
pytest -q
```

```bash
ruff check src tests
```

測試只透過公開切點驗證：批量入口 `fcn_checker.batch`（`check_batch`／`save_batch`／`run_batch`）、`fcn-batch` CLI 與 PANEL 工作階段 `fcn_checker.panel_workflow.PanelSession`。合成說明書 PDF 由各上手的合成器（`tests/synth.py`、`tests/hsbc_synth.py`）產生，合成參考條件表與單份核對 harness 在 `tests/reference_synth.py`、`tests/harness.py`，數值皆虛構。`tests/test_real_samples*.py` 只在本機 `data/` 有真實樣本時執行，CI 自動略過。

## 文件與開發規則

- [架構與模組邊界](docs/architecture.md)
- [資料契約草案](docs/data-contract.md)
- [分階段 TODO 與待確認項目](docs/TODO.md)
- [新增上手（issuer）實作規範](docs/issuer-onboarding.md)
- [名詞表](CONTEXT.md)、[參考條件表格式](docs/order-formats/reference-sheet.md)（設定檔 `config/reference_sheet.toml`、`config/issuer_prefixes.toml`）
- [BARC 範本規格](docs/templates/barc-zh-product-description.md)、[BARC 詢價格式（已刪除，僅供回溯）](docs/order-formats/barc-inquiry.md)、[BARC 核對規則](docs/rules/barc-check-rules.md)、[審查標準](docs/rules/review-standard.md)（設定檔 `config/review_standard.toml`）
- ADR：[0001 第一版採規則式核對](docs/adr/0001-deterministic-runtime.md)、[0002 本機 Python CLI／PyMuPDF](docs/adr/0002-python-cli-pymupdf.md)、[0003 PANEL 以公開 GitHub main 更新](docs/adr/0003-public-github-panel-update.md)、[0004 核對條件統一改用參考條件表](docs/adr/0004-reference-sheet-as-check-source.md)
- [AGENTS.md](AGENTS.md)：共用開發規範；[CLAUDE.md](CLAUDE.md) 沿用此規範。
- `.github/ISSUE_TEMPLATE/`、PR 範本、CI 皆保留自原始 template。

開發流程：Issue → branch/worktree → plan → implementation → validation → commit/push → PR → review/CI → merge。CI 檢查 patch 空白、ruff lint／format 與 pytest（Python 3.11、3.13，只用合成資料）。

## 資料管理

真實 PDF、下單檔、擷取文字、OCR 影像與報告放在被 Git 忽略的 `data/` 或 `runtime/`。目前慣例：TS 放 `data/ts/`、下單 Excel 放 `data/`、探勘輸出與暫存檔放 `data/tmp/`、核對報告預設輸出到 `runtime/reports/`。不要把客戶資料、交易細節或憑證貼到公開文件、Issue、PR、測試快照及 CI artifact。資料保存期限與存取權限於導入前確認。

## 範本來源

由 [TaylorYam/ai-project-template](https://github.com/TaylorYam/ai-project-template) 使用 GitHub Template 建立，沿用私人可見性。初始化需求見 [Issue #1](https://github.com/TaylorYam/fcn-term-sheet-checker/issues/1)。
