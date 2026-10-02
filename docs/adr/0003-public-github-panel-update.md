# 0003: PANEL 以公開 GitHub main 提供手動更新

- Status: Deprecated（2026-10-02，Issue #85）
- Date: 2026-10-01

> 2026-10-02 停用：PANEL 只有維護者本人使用，同事不安裝也不使用，「各同事獨立本機安裝」的前提已不成立。整套自動更新（按鈕、`PanelUpdater`、`.local/releases/` 版本資料夾與啟動指標、更新鎖）已移除；維護者在專案資料夾 `git pull` 後重新執行 `setup_panel.cmd`。核對紀錄的程式 commit 改取開發用 Git 的 HEAD 或安裝時記錄的 `.local/installed.json`。以下為原決策紀錄。

## Context

使用者要求在 PANEL 新增更新 GitHub 最新版按鈕，並確認之後會公開 Repo。各同事獨立本機安裝，不要求 Git、GitHub 登入或保存 Token。既有核對結果與本機審查設定不能在更新中被覆寫。相關工作：Issue #22。

## Decision

- 使用固定 `TaylorYam/fcn-term-sheet-checker` 公開 Repo 的 `main`，先檢查 commit，再以該完整 SHA 下載 ZIP。匿名 REST API 存取失敗時不更新；本功能不變更 Repo 可見性。
- 手動按鈕觸發；有新版時提醒保存結果，經使用者確認才安裝、重啟。不排程或開機自動更新。
- 更新服務是獨立公開入口 `PanelUpdater`；HTTP 與程序是外部測試邊界。UI 背景執行檢查、下載、安裝與重啟，主執行緒只呈現進度與詢問。
- ZIP 限制下載／解壓大小、拒絕越界路徑與 symlink，僅擷取程式、套件設定、啟動檔與內建 config。新版在 `.local/releases/<SHA>-<隨機碼>` 建立獨立 venv，依 `constraints.txt` 安裝並驗證 import，再原子更新 `.local/current.json`。
- 穩定的根目錄啟動器依本機指標啟動新版；仍使用根目錄 `config`。更新程式及套件，不自動修改作業審查設定；新版內建設定留在版本資料夾供維護人員比較。預覽與核對仍綁定實際使用設定的 hash。
- 舊版本、PDF、Excel、報告及本機設定保留，不上傳。新版啟動失敗會回復指標並保留原視窗；不自動清除版本資料夾。更新鎖防止同一資料夾同時安裝。

## Alternatives considered

- `git pull` 覆寫目前專案與 venv：要求 Git 並容易改到使用者設定，執行中的環境也可能半更新。
- GitHub Releases：日後可加入穩定發版；目前使用者要求直接取 GitHub 最新版，採已合併的 `main`。
- 私有 Repo Token：使用者已選擇公開 Repo，程式不保存 GitHub 憑證。

## Consequences

更新需要 GitHub 與 Python 套件來源可連線；日常核對仍完全在本機。信任此 Repo 的已合併程式，不增加簽章或第三方更新服務。保留版本會占用磁碟；非正常中斷留下的更新鎖需維護人員確認程序停止後處理。首次由 ZIP 安裝可能沒有 commit 紀錄，會顯示未知並於第一次更新建立紀錄。審查設定變更需另外人工確認與導入。
