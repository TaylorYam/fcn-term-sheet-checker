# 下單資料格式：HSBC（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../config/reference_sheet.toml)
- 狀態：2026-10-02 起，所有上手都用參考條件表（[ADR 0004](../adr/0004-reference-sheet-as-check-source.md)）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 HSBC 的說明書來源與差異

## 1. 結構

| 項目 | 內容 |
|---|---|
| 工作表 | `樣本清單`（同檔另有 `詢價表格`，為 BARC 詢價表，HSBC 不使用） |
| 表頭 | 第 3 列 |
| 資料 | 第 4 列起，每列一檔商品，多家上手混在同一張表 |
| 配對鍵 | `TDCC Code` = 說明書商品代號（12 位） |
| 上手 | `發行機構` 欄；HSBC 列為 `HSBC` |
| 空值 | `-` |
| 日期 | Excel 日期值 |
| 百分比 | 百分比數字（`75` 代表 75%） |
| 浮點尾數 | 例 `UF`、`Coupon p.a. (%)` 可能出現 `x.xx0000000005`；先四捨五入到 9 位 |

讀取規則：

- 以說明書商品代號找 `TDCC Code` 相同的列；找不到、或找到多列 → 轉人工覆核。同一批有多份說明書對到同一列 → 全部轉人工覆核（見 [reference-sheet.md](reference-sheet.md) §2）。
- 找到的列 `發行機構` 必須是 `HSBC`，否則轉人工覆核。
- 表頭不在設定檔（`columns` 或 `ignored`）中的欄名 → 轉人工覆核；沒有表頭的欄位一律忽略（目前最右側有一個無表頭欄，存放已 KO 註記）。

## 2. 欄位對照

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/hsbc-zh-product-description.md)） |
|---|---|---|
| TDCC Code | `product_code` | 封面商品代號 |
| ISIN Code | `isin` | 第一章 §27 |
| 單位面額 | `denomination` | 第一章 §6 |
| 承作幣別 | `currency` | 封面計價幣別 |
| 交易日／發行日／最終比價日／到期日 | `trade_date`／`issue_date`／`final_valuation_date`／`maturity_date` | §15(6)(2)(5)(3) |
| KO(%) | `ko_pct` | §11(2) |
| KO(Freq) | `ko_observation` | §11(2) 寫法（範本規格 §4.2） |
| KO(memo) | `ko_memory` | 名稱含「記憶式」 |
| K(%) | `strike_pct` | §11(3) |
| KI(%) | `ki_pct` | §11(3)；無 KI 時 Excel 為 `-` |
| KI(Freq) | `ki_type` | §11(3) 觸及不保本事件決定日 |
| Coupon p.a. (%) | `coupon_pa_pct` | §11(1) |
| 天期(月) | `tenor_months` | §15(1) |
| Non-Call(月) | `first_callable_period` | 範本規格 §4.3 |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| UL_n | `underlying_n` | §12(1) 彭博代號 |
| UL_n_進場價／執行價／下限價／KO價 | `underlying_n_initial_price`／`_strike_price`／`_ki_price`／`_ko_price` | §12(1) 價格表 |

## 3. 允許值

| 欄 | 值 | 意義 |
|---|---|---|
| KO(Freq) | `D` | 提前出場每日觀察 |
| | `P` | 提前出場定期觀察 |
| KO(memo) | `Y`／`N` | 記憶式／非記憶式 |
| KI(Freq) | `-` | 無 KI |
| | `AM` | 到期觀察（觸及不保本事件決定日為最後評價日） |
| | `D` | 每日觀察（觸及不保本事件決定日為每個預定交易日） |

不在表內的值 → 轉人工覆核。

## 4. 比價日填法（共用規則見[參考條件表格式](reference-sheet.md) §4）

- **P**：每個可提前出場的期別都要填，等於該期自動提前到期決定日；不可提前出場的期別填 `-`。期別以「提前出場付款日 = 配息表付息日」對應，不使用提前出場表的「計息期間」流水號。
- **D**：填第一個可提前出場的期別（KO 起日所在期，等於 KO 起日）與最後一期（等於最終比價日）；其他期別填 `-`（Issue #69）。

該填未填、不該填卻有值，都算不一致。

## 5. 不核對的欄位

`庫存狀態`、`當日比價`、`Product`、`私銀註記`、`UF`、`UL_n_Memo`、無表頭欄。`發行機構` 只用來確認列屬於 HSBC，不另外比對。
