# FCN Term Sheet Checker

FCN Term Sheet 自動核對專案：將條款文件與已確認的下單資料轉成相同資料結構，以可追溯規則產生差異與人工覆核清單。

**目前狀態：可用——BARC 中文產品說明書（文字型 PDF）× BARC 詢價表的本機核對 CLI**，含主要條款、配息表與提前出場表、保證配息期、日期規則與審查標準。尚未涵蓋的 Monthly KI、標的中文名稱與 ISIN 會列在報告的「未涵蓋」區。第一版 production runtime 不使用 LLM；LLM 可協助開發，但不參與正式擷取或判定。

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
- 下單資料來源：各家上手原始格式（每家一份格式設定）。BARC 為詢價表，一筆交易一個檔，以儲存格 B3 的商品代號配對：[BARC 詢價格式](docs/order-formats/barc-inquiry.md)、[核對規則](docs/rules/barc-check-rules.md)。舊整理表 `FCN參考條件.xlsx` 已停用。
- 本機探勘：BARC 詢價表樣本與說明書 41 項全部一致；14 份說明書的 PDF 內部規則全部成立；文件資訊、日期規則與[審查標準](docs/rules/review-standard.md)已確認；標的目前只核對英文代號，中文名稱核對擱置（核對規則 §6.2）。

## 安裝

需要 Python ≥ 3.11。PDF 擷取使用 PyMuPDF（AGPL-3.0；本工具僅限公司內部本機使用），Excel 讀取使用 openpyxl；已驗證版本鎖定在 `constraints.txt`。

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

## 使用方式

在專案根目錄執行（審查標準與詢價格式預設讀 `config/`）：

```bash
fcn-check data/ts/<商品代號>_TS.pdf data/<詢價表>.xlsx --out runtime/reports
```

| 參數 | 說明 |
|---|---|
| 第 1 個 | 說明書 PDF（BARC 中文產品說明書） |
| 第 2 個 | BARC 詢價表 Excel（一筆交易一個檔，B3 為商品代號） |
| `--review-standard` | 審查標準設定檔，預設 `config/review_standard.toml` |
| `--order-format` | 詢價格式設定檔，預設 `config/order_formats/barc.toml` |
| `--out` | 報告輸出資料夾，預設 `runtime/reports`（被 Git 忽略） |

輸出 `<PDF 檔名>.check.json`（完整逐項結果、證據與執行 metadata）與 `<PDF 檔名>.check.md`（人看的報告：先列不一致與需人工覆核項目，再列未涵蓋規則與通過項目）。每項結果附詢價表值、說明書值、說明書頁碼與原文、詢價表儲存格位置。

| 結束碼 | 整體狀態 |
|---|---|
| 0 | `PASS`：已涵蓋的規則全部通過（未涵蓋規則仍須人工核對） |
| 1 | 有 `MISMATCH`（不一致）或 `REVIEW_REQUIRED`（需人工覆核） |
| 2 | `ERROR`：PDF 損毀／加密、檔案不存在、設定檔錯誤等 |

整體狀態優先順序 `ERROR > REVIEW_REQUIRED > MISMATCH > PASS`。抓不到的欄位、多個不同值、非 BARC 範本、詢價表未知欄名或欄位值一律轉人工覆核，不猜值。

## 開發與測試

```bash
pytest -q
```

```bash
ruff check src tests
```

測試只透過兩個切點驗證：核對入口 `fcn_checker.checker.run_check`（合成說明書 PDF 與合成詢價表，於測試時由 `tests/synth.py` 產生，數值皆虛構）與 `fcn-check` CLI。`tests/test_real_samples.py` 只在本機 `data/` 有真實樣本時執行，CI 自動略過。

## 文件與開發規則

- [架構與模組邊界](docs/architecture.md)
- [資料契約草案](docs/data-contract.md)
- [分階段 TODO 與待確認項目](docs/TODO.md)
- [BARC 範本規格](docs/templates/barc-zh-product-description.md)、[BARC 詢價格式](docs/order-formats/barc-inquiry.md)（設定檔 `config/order_formats/barc.toml`）、[BARC 核對規則](docs/rules/barc-check-rules.md)、[審查標準](docs/rules/review-standard.md)（設定檔 `config/review_standard.toml`）
- [ADR：第一版採規則式核對](docs/adr/0001-deterministic-runtime.md)
- [AGENTS.md](AGENTS.md)：共用開發規範；[CLAUDE.md](CLAUDE.md) 沿用此規範。
- `.github/ISSUE_TEMPLATE/`、PR 範本、CI 皆保留自原始 template。

開發流程：Issue → branch/worktree → plan → implementation → validation → commit/push → PR → review/CI → merge。CI 檢查 patch 空白、ruff lint／format 與 pytest（Python 3.11、3.13，只用合成資料）。

## 資料管理

真實 PDF、下單檔、擷取文字、OCR 影像與報告放在被 Git 忽略的 `data/` 或 `runtime/`。目前慣例：TS 放 `data/ts/`、下單 Excel 放 `data/`、探勘輸出與暫存檔放 `data/tmp/`、核對報告預設輸出到 `runtime/reports/`。不要把客戶資料、交易細節或憑證貼到公開文件、Issue、PR、測試快照及 CI artifact。資料保存期限與存取權限於導入前確認。

## 範本來源

由 [TaylorYam/ai-project-template](https://github.com/TaylorYam/ai-project-template) 使用 GitHub Template 建立，沿用私人可見性。初始化需求見 [Issue #1](https://github.com/TaylorYam/fcn-term-sheet-checker/issues/1)。
