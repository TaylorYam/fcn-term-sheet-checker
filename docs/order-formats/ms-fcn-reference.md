# 下單資料格式：MS（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../config/reference_sheet.toml)
- 狀態：規格已確認（Issue #132），已實作（Issue #135）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 MS 的說明書來源與差異

## 1. 結構

與其他上手相同（`樣本清單` 工作表、第 3 列表頭、每列一檔）。MS 列的 `發行機構` 欄寫 `MS`；商品代號前 3 碼為 `147`。

實作時要登記：

- `config/issuer_prefixes.toml`：`"147" = "MS"`。
- `config/reference_sheet.toml`：MS 在 `發行機構` 欄的寫法 `MS`。

## 2. 欄位對照

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/ms-zh-product-description.md)） |
|---|---|---|
| TDCC Code | `product_code` | 封面 1 商品代號 |
| ISIN Code | `isin` | 封面 3 |
| 單位面額 | `denomination` | 第一章 §6 |
| 承作幣別 | `currency` | 封面 9 |
| 交易日／發行日／最終比價日／到期日 | `trade_date`／`issue_date`／`final_valuation_date`／`maturity_date` | §14(2)／(3)／(5)／(4) |
| KO(%) | `ko_pct` | §16 價格表表頭 `自動提前出場價（期初價格的X%）`；表上沒有這欄時見 §3 |
| KO(Freq) | `ko_observation` | §14(6) 表型＋§17（範本規格 §4.1） |
| KO(memo) | `ko_memory` | 名稱＋§17（範本規格 §4.2） |
| K(%) | `strike_pct` | §16 表頭 `執行價（期初價格的X%）` |
| KI(%) | `ki_pct` | §16 表頭 `下限價格（期初價格的X%）`；無 KI 時 Excel 為 `-` |
| KI(Freq) | `ki_type` | §16 `觸及下限事件：` 定義句（範本規格 §4.4） |
| Coupon p.a. (%) | `coupon_pa_pct` | **說明書沒有年利率**：改核對 §15 月配息率 = 年利率 ÷ 12、§18 獲利情境年化報酬率 = 年利率（[MS 核對規則](../rules/ms-check-rules.md) §3.3） |
| 天期(月) | `tenor_months` | §14(1) |
| Non-Call(月) | `first_callable_period` | §17（範本規格 §4.3） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| 期初定價 | `initial_pricing` | 說明書沒有對應資料，只決定價格欄怎麼處理（共用規則） |
| UL_n | `underlying_n` | §16 價格表彭博代碼（§11 交叉驗證） |
| UL_n_進場價／執行價／下限價／KO價 | `underlying_n_initial_price`／`_strike_price`／`_ki_price`／`_ko_price` | §16 價格表 |

## 3. 允許值與 MS 差異

| 欄 | 值 | 意義 |
|---|---|---|
| KO(Freq) | `D`／`P` | 每日觀察／每期定價日觀察 |
| KO(memo) | `Y`／`N` | 記憶式／非記憶式 |
| KI(Freq) | `-` | 無 KI |
| | `AM` | 到期觀察（`若在期末定價日`） |
| | `D` | 每日觀察（`若在交易日（含）至期末定價日（含）間的任一共同預定交易日`） |
| | `P` | **新增**：每期觀察（`若在任一配息週期終止日(含期末定價日)`）。2026-10-08 確認新增，所有上手適用；BARC、HSBC 範本沒有這種 KI，說明書不會判成 `P` |

Non-Call = 天期的 P 型（範本規格 §4.6，S05 型）：說明書價格表沒有「自動提前出場價」欄，也沒有 KO %。這時 `KO(%)` 與 `UL_n_KO價` **不核對**（結果列為不適用並註明「說明書沒有自動提前出場價」），其他欄照常核對（2026-10-08 確認）。Non-Call < 天期時價格表一定要有這欄，缺欄轉人工覆核。

## 4. 比價日填法（共用規則見[參考條件表格式](reference-sheet.md) §4）

| KO(Freq) | 填法 | 說明書日期 |
|---|---|---|
| `D` | `比價日_{Non-Call}` 與 `比價日_{期數}` | 開始：§14(6) 第 Non-Call 期的配息週期終止日（= §17 觀察起日）；最後：末期終止日（= 期末定價日） |
| `P` | `比價日_{Non-Call}` 到 `比價日_{期數}` 每一期 | §14(6) 各期定價日 |

新版 6 份樣本的比價日全部符合此填法（S05 Non-Call = 天期，只填最後一格）。

## 5. 不核對的欄位

`庫存狀態`、`當日比價`、`Product`、`私銀註記`、`UF`、`UL_n_Memo`、無表頭欄（同其他上手）。`發行機構` 只用來確認列屬於 MS。
