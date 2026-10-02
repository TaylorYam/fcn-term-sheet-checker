# 0006: PANEL 只供維護者使用，移除自動更新

- Status: Accepted
- Date: 2026-10-02

## Context

[ADR 0003](0003-public-github-panel-update.md) 假設各同事會在自己的電腦獨立安裝 PANEL，所以在 PANEL 內建「更新 GitHub 最新版」：下載公開 Repo 的 ZIP、在 `.local/releases/` 建立版本資料夾與 venv、切換啟動指標並重新啟動。2026-10-02 使用者確認 PANEL 只有維護者本人使用，同事不安裝也不使用；維護者直接在專案資料夾用 Git 取得新版。相關工作：Issue #85。

## Decision

- 移除整套 PANEL 自動更新：更新按鈕與流程、`PanelUpdater`、版本資料夾、啟動指標、更新鎖，以及只為更新重新啟動而存在的啟動參數。
- 更新方式：在專案資料夾 `git pull`，再重新執行 `setup_panel.cmd`（安裝是非 editable，只 `git pull` 不生效）。
- 雙擊入口一律從專案根目錄啟動，不讀舊版留下的 `.local/current.json`。
- 核對紀錄的程式 commit 取開發用 Git 工作目錄的 HEAD；取不到時用 `setup_panel.cmd` 安裝時記錄的 `.local/installed.json`。

## Alternatives considered

- 只拿掉按鈕、保留更新程式：留下約 600 行用不到的程式與測試，還要維護版本資料夾的啟動分支。
- 保留自動更新給未來的同事：目前沒有這個需求；日後真的需要，再依新需求另立 ADR。

## Consequences

程式與安裝流程變簡單，日常核對不需要連網。維護者每次更新都要記得重跑 `setup_panel.cmd`。舊版自動更新留下的 `.local/releases/`、`.local/current.json`、`.local/update.lock` 不再使用，由維護者手動刪除；`.local/installed.json` 保留。若日後同事也要使用 PANEL，需重新設計發佈與更新方式。
