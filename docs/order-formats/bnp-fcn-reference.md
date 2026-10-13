# 下單資料格式：BNP（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../config/reference_sheet.toml)
- 狀態：規格已確認（Issue #189），**尚未實作**。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 BNP 的說明書來源與差異

## 1. 結構

與其他上手相同（`樣本清單` 工作表、第 3 列表頭、每列一檔）。BNP 列的 `發行機構` 欄寫 `BNP`；商品代號前 3 碼為 `082`（`config/issuer_prefixes.toml` 已登記）。

實作時要登記：

- `config/reference_sheet.toml`：BNP 在 `發行機構` 欄的寫法 `BNP`。
- `src/fcn_checker/issuers.py`：BNP 範本（`bnp-zh-pd`）。

## 2. 欄位對照

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/bnp-zh-product-description.md)） |
|---|---|---|
| TDCC Code | `product_code` | 封面 1 `TDCC 商品代號為` |
| ISIN Code | `isin` | 封面 1 `ISIN 為` |
| 單位面額 | `denomination` | 第一章 §6 |
| 承作幣別 | `currency` | 封面 9（`美元`→USD、`日圓`→JPY、`英鎊`→GBP） |
| 交易日／發行日／最終比價日／到期日 | `trade_date`／`issue_date`／`final_valuation_date`／`maturity_date` | §14(2) 期初訂價日／(3) 發行日／(4) 期末評價日／(5) 到期日 |
| KO(%) | `ko_pct` | §11 `自動提前贖回價格` 列的 `即期初價格之X%`（Non-Call = 天期時也有） |
| KO(Freq) | `ko_observation` | §14(6)(7)(8) 寫法＋日期表表頭（範本規格 §4.1） |
| KO(memo) | `ko_memory` | §16 自動提前贖回事件定義句（範本規格 §4.2） |
| K(%) | `strike_pct` | §11 `執行價格` 列的 `即期初價格之X%` |
| KI(%) | `ki_pct` | §11 `下限價格(Knock-In Price)` 列的 `即期初價格之X%`；無 KI 時 Excel 為 `-` |
| KI(Freq) | `ki_type` | §17 `「觸及生效事件觀察日」：指` 定義句（範本規格 §4.4） |
| Coupon p.a. (%) | `coupon_pa_pct` | §15 `(即年利率Y%)`（共用規則直接適用） |
| 天期(月) | `tenor_months` | §14(1) |
| Non-Call(月) | `first_callable_period` | D 型 §14(8) 的 k；P 型日期表第一個有日期的期別（範本規格 §4.3） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| 期初定價 | `initial_pricing` | 說明書沒有對應資料（VWAP 商品也沒有 VWAP 字樣），只決定價格欄怎麼處理（共用規則，Issue #122） |
| UL_n | `underlying_n` | §11 `彭博代碼：` 列，依欄位順序 |
| UL_n_進場價／執行價／下限價／KO價 | `underlying_n_initial_price`／`_strike_price`／`_ki_price`／`_ko_price` | §11 期初價格／執行價格／下限價格／自動提前贖回價格 |

## 3. 允許值與 BNP 差異

| 欄 | 值 | 意義 |
|---|---|---|
| KO(Freq) | `D`／`P` | 每日觀察（§14(8) `每一個預定交易日`）／每期觀察（日期表「自動提前贖回評價日」） |
| KO(memo) | `Y`／`N` | 記憶式（`皆曾經於…或之前任一`）／非記憶式 |
| KI(Freq) | `-` | 無 KI（§17 沒有觸及生效事件） |
| | `AM` | 到期觀察（`指期末評價日`） |
| | `D` | 每日觀察（`每一個預定交易日`） |
| | `P` | 每期觀察（`每一個配息期結束日`）；BN2 樣本實證（MS 新增此值時尚無新版樣本） |

- Non-Call = 天期（BN1）：說明書仍有自動提前贖回價格與 %，`KO(%)`、`UL_n_KO價` 照常核對（與 MS 不同）。
- 期初定價 `VWAP`（BN5）：照共用規則，`UL_n` 四個價格欄不比對，整份通過或人工放行後以說明書價格覆寫（Issue #122）。BN5 表上的值本來就等於說明書。
- 單位面額：USD／GBP 10,000、JPY 1,000,000（= 審查標準預設值）。

## 4. 比價日填法（共用規則見[參考條件表格式](reference-sheet.md) §4）

| KO(Freq) | 填法 | 說明書日期 |
|---|---|---|
| `D` | `比價日_{Non-Call}` 與 `比價日_{期數}` | 開始：日期表第 Non-Call 期的配息期結束日（= §14(8) 觀察起日）；最後：末期結束日（= 期末評價日） |
| `P` | `比價日_{Non-Call}` 到 `比價日_{期數}` 每一期 | 日期表各期自動提前贖回評價日（`不適用` 的期別填 `-`） |

6 份有列的樣本（BN1–BN3、BN5–BN7）的比價日全部符合此填法（BN1 Non-Call = 天期，只填最後一格）。

## 5. 不核對的欄位

`庫存狀態`、`當日比價`、`Product`、`私銀註記`、`UF`、`UL_n_Memo`、無表頭欄（同其他上手）。`發行機構` 只用來確認列屬於 BNP。

## 6. 探勘時發現的表上問題（2026-10-13）

- BN1 的 `UL_1` 四個價格與說明書差 5 倍（表上為說明書的 1/5，該標的在交易後有股票分割）：核對時會判不一致，屬預期；表上值要不要改回說明書原值由作業人員決定。
