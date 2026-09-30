# FCN Term Sheet Checker

FCN Term Sheet 自動核對專案：將條款文件與已確認的下單資料轉成相同資料結構，以可追溯規則產生差異與人工覆核清單。

**目前狀態：只有架構與工作規劃，尚無可執行的核對功能。** 第一版 production runtime 不使用 LLM；LLM 可協助開發，但不參與正式擷取或判定。

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

## 明天開工（2026-10-01）

1. 閱讀 [AGENTS.md](AGENTS.md)、[架構](docs/architecture.md) 與 [TODO](docs/TODO.md)。
2. 選定一家 issuer、一個範本版本，確認人工核准的下單資料來源及第一批必核欄位。
3. 將可合法使用的去識別樣本放在本機 `data/`；Git 只放經檢查的合成 fixture。
4. 依 TODO 建立第一個實作 Issue，再從最新 `main` 建立分支；完成一份文字型 PDF 的最小端到端流程。
5. 加入對應測試與 Python CI，經 PR review／CI 通過後合併。

尚未選定套件版本與 Python 最低版本，沒有安裝或執行指令；第一個實作 Issue 會補齊環境設定、依賴鎖定及 CLI 使用方式。

## 文件與開發規則

- [架構與模組邊界](docs/architecture.md)
- [資料契約草案](docs/data-contract.md)
- [分階段 TODO 與待確認項目](docs/TODO.md)
- [ADR：第一版採規則式核對](docs/adr/0001-deterministic-runtime.md)
- [AGENTS.md](AGENTS.md)：共用開發規範；[CLAUDE.md](CLAUDE.md) 沿用此規範。
- `.github/ISSUE_TEMPLATE/`、PR 範本、CI 皆保留自原始 template。

開發流程：Issue → branch/worktree → plan → implementation → validation → commit/push → PR → review/CI → merge。目前 CI 僅檢查 patch 空白，不能代表金融欄位或核對邏輯已驗證。

## 資料管理

真實 PDF、下單檔、擷取文字、OCR 影像與報告放在被 Git 忽略的 `data/` 或 `runtime/`。不要把客戶資料、交易細節或憑證貼到公開文件、Issue、PR、測試快照及 CI artifact。資料保存期限與存取權限於導入前確認。

## 範本來源

由 [TaylorYam/ai-project-template](https://github.com/TaylorYam/ai-project-template) 使用 GitHub Template 建立，沿用私人可見性。初始化需求見 [Issue #1](https://github.com/TaylorYam/fcn-term-sheet-checker/issues/1)。
