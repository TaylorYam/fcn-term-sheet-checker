# 資料契約草案

此文件是實作討論起點，不是已完成的 JSON Schema。先確認首個範本與下單來源，再實作版本化 schema 與必填矩陣。

## 輸入與來源證據

- `Document`：document_id、sha256、頁數、逐頁 extraction method、工具版本與擷取錯誤。
- `Evidence`：頁碼（從 1 起）、bbox（使用統一座標系，含頁面尺寸與旋轉處理）、原文、擷取方式。OCR 另留引擎分數；分數不等於欄位正確機率。
- `ParsedField`：欄位路徑、raw_value、候選值、證據清單及狀態：present / missing / ambiguous / invalid / not_applicable。衝突值保留，不覆寫。
- `OrderRecord`：預期條款、來源檔案 hash、列／儲存格位置、來源版本與交易識別。正式來源待確認；開發先用合成 JSON。

## 核對欄位候選

| 欄位 | 標準化草案 | 必須確認的語意 |
|---|---|---|
| issuer、product_type、trade_id | 穩定識別碼／受控字串 | 發行人與保證人分開；交易配對依據 |
| currency、notional | ISO 幣別與 Decimal 字串 | 金額單位、面額與總本金不可混用 |
| coupon | rate、basis、frequency、day_count | 年率／每期率／金額分開；18.25% → `0.1825` 僅在比例語意確認後 |
| underlyings[] | identifier、scheme、exchange、currency | ticker 不能單獨代表唯一標的；多標的逐筆配對 |
| strike、knock_in、knock_out | level、level_type、reference、observation_type | 比例／絕對價格、參照初始價格、觀察方式與門檻包含等號 |
| trade_date、issue_date、maturity_date | ISO 日期 | 不等同觀察日／付款日；不猜模糊日期 |
| observation_schedule、payment_schedule | 日期清單與語意 | 跨頁表格、營業日調整、順序與重複值 |

第一個範本（BARC）的實際欄位、變化型態與下單資料對照見 [BARC 範本規格](templates/barc-zh-product-description.md) 、[BARC 核對規則](rules/barc-check-rules.md) 與 [BARC 詢價格式](order-formats/barc-inquiry.md)。BARC 樣本中觀察到：KI 有「無／到期觀察／每日觀察」三種、提前出場有「定日／期間」兩種觀察方式，且月配息率為主值、年利率由其推得。

未使用 KI 的產品不應被強迫補值。產品範本需明確定義適用欄位與必要條件。首個 slice 只實作選定必核欄位；不聲稱已完整核對整份法律文件。

## 規則結果與例外報告

每筆 `CheckResult` 至少含 rule_id、rule_version、field_path、status、expected、actual、tolerance、reason_code、document_evidence、order_evidence。數值及日期先驗證語意再比較；缺值、歧義、未知單位或不支援條款產生覆核例外。

報告包括 scope（實際支援的範本與欄位）、coverage（已核對／缺漏／未支援）、整體狀態、所有逐欄結果、執行版本與來源 hash。不能把未核對欄位隱藏後顯示「整份通過」。

人工覆核決定另記操作者、時間與理由，不覆寫原始機器結果。覆核操作介面與保存方式屬後續工作。
