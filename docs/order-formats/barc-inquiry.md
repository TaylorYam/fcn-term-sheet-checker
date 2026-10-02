# 下單資料格式：BARC 詢價表（已停用）

> **2026-10-02 起停用**（[ADR 0004](../adr/0004-reference-sheet-as-check-source.md)）：核對條件改用[參考條件表](reference-sheet.md)（`fcn-batch`）。PANEL 與 `fcn-check` 還在使用這個格式，等 PANEL 改用參考條件表時，連同程式與設定一起刪除。

- 設定檔：[`config/order_formats/barc.toml`](../../config/order_formats/barc.toml)
- 核對規則：[BARC 核對規則](../rules/barc-check-rules.md) §3.2、§3.3
- 狀態：2026-10-01 依 1 份真實樣本建立（本機 `data/BARC詢價格式.xlsx`，不進 Git）；其他型態暫無樣本，依規則推得（見核對規則 §5）
- 檔案單位：一筆交易一個檔（暫定）

## 1. 版面

| 項目 | 內容 |
|---|---|
| 工作表 | `詢價表格` |
| 商品代號 | 儲存格 `B3`（12 位，文字），用來配對說明書 PDF |
| 表頭 | 第 4 列，從 B 欄開始 |
| 資料 | 第 5 列（一筆交易一列） |
| 公式、合併儲存格、資料驗證 | 無 |
| 百分比 | 以「百分比數字」儲存：`63.13` 代表 63.13% |
| 日期 | Excel 日期值 |
| 空值 | 空白儲存格 |

## 2. 欄位

| 欄 | 欄名 | 標準欄位 | 說明 |
|---|---|---|---|
| B | Product | — | 例 `FCN`；不核對 |
| C | Currency | 幣別 | ISO 代碼 |
| D | Guaranteed Periods (m) | 保證配息期 | 保證配息、不會被 KO 的期數；與 PDF 的對應見核對規則 §3.3 |
| E–I | BBG Code 1～5 | 標的 1～5 | 彭博代號，不含 ` Equity`，例 `XXX UN` |
| J | Strike (%) | 執行 % | |
| K | KO Type | KO 觀察方式＋記憶式 | 值見 §3 |
| L | KO Barrier (%) | KO % | |
| M | Coupon p.a. (%) | 年利率 | |
| N | Upfront / Note Price (%) | — | 不核對 |
| O | Tenor (m) | 天期 | |
| P | Barrier Type | KI 型態 | 值見 §3 |
| Q | KI Barrier (%) | KI % | 無 KI 時為 `0` |
| R | Observation Frequency (m) | 觀察頻率 | 月；配息期數 = 天期 ÷ 頻率 |
| S | OTC | — | 例 `Note`；不核對 |
| T | Funding Spread (bps) | — | 不核對 |
| U | Effective Date offset | 發行日偏移 | 發行日 − 交易日的日曆天數 |
| V | Notional | — | 總名目本金，不是單位面額；不核對 |
| W | Trade Date | 交易日 | |
| X | Issue Date | 發行日 | |
| Y | Final Valuation Date | 最終評價日 | |
| Z | Maturity Date | 到期日 | |
| AA | Quote ID | — | 上手報價編號；不核對 |

設定檔沒有列出的欄名出現時 → 轉人工覆核（格式可能已改版）。

## 3. 欄位值

| 欄位 | 允許值 | 意義 |
|---|---|---|
| KO Type | `Daily Memory` | 期間每日觀察、記憶式 |
| | `Daily` | 期間每日觀察、非記憶式 |
| | `Period End Memory` | 期末定日觀察、記憶式 |
| | `Period End` | 期末定日觀察、非記憶式 |
| Barrier Type | `None` | 無 KI |
| | `EKI` | 到期觀察 KI |
| | `AKI` | 每日觀察 KI |
| | `MKI` | Monthly KI：每月觀察 KI（尚無樣本，PDF 判斷方式為推測） |

其他值 → 轉人工覆核。

## 4. 詢價表沒有、說明書有的資料

| 資料 | 處理方式 |
|---|---|
| ISIN | 暫不核對 |
| 單位面額 | 依[審查標準](../rules/review-standard.md)的幣別預設值；不等於預設值轉人工審查 |
| 各期觀察日、配息日 | PDF 內部日期規則（核對規則 §3.8） |
| 各標的進場價、執行價、KO 價、下限價 | 詢價時尚未定價；PDF 內部檢查執行／KO／下限價 = 進場價 × 百分比（四捨五入 4 位） |
| KI 觀察方式 | 由 Barrier Type 推得 |
