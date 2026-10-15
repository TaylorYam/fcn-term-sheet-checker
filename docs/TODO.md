# TODO 與開工清單

## 0. 明天先確定範圍

- [x] 選一家 issuer、一個範本版本；確認樣本為文字／掃描／混合 PDF。→ 巴克萊（BARC）中文產品說明書，14 份樣本皆為文字型 PDF。見 [BARC 範本規格](templates/barc-zh-product-description.md)。
- [ ] 取得可合法使用且去識別的樣本，建立人工標註的 expected output；真實樣本留在 Git 外。（真實樣本已放本機 `data/term-sheets/`；去識別 fixture 尚未建立）
- [x] 確認權威下單來源（CSV、Excel、系統匯出或其他）與交易配對方式。→ 2026-10-02 起所有上手共用參考條件表，以商品代號對 `TDCC Code`；見[參考條件表格式](order-formats/reference-sheet.md)、[ADR 0004](adr/0004-reference-sheet-as-check-source.md)。（BARC 詢價表已停用，PANEL 改版前保留）
- [ ] 與作業人員確認首批必核欄位、年率／期率、門檻語意、日期與容差規則。（交易條款、文件資訊與日期規則皆已確認；標的中文名稱核對擱置，見核對規則 §6.2）
- [x] 確認本機 Python 版本、套件授權與作業環境；鎖定依賴。→ Python ≥ 3.11；PyMuPDF（AGPL-3.0，僅公司內部本機使用）、openpyxl；版本鎖定於 `constraints.txt`（Issue #7）。

- [x] 月配息率容差（≤ 0.0001）已確認；標的只核對彭博英文代號（含交易所尾碼）。
- [x] 文件資訊、日期規則與[審查標準](rules/review-standard.md)已確認（`config/review_standard.toml`）。
- [ ] （擱置）標的中文名稱與交易所的對照表來源與維護方式（[核對規則](rules/barc-check-rules.md) §6.2）。

上述選擇在對應實作 Issue 解決，不阻擋本次文件初始化。

## 1. 最小垂直流程：一家 issuer、一個文字範本

建立實作 Issue，將範圍切到一次 PR 可完成：

- [x] 建立 Python package、可重現安裝方式、CLI 與 pytest／lint CI；保留既有 whitespace check。（Issue #7）
- [x] 擷取文字區塊／頁碼／座標，辨識範本版本；未知／多重命中回報覆核。（Issue #7）
- [x] 實作最小 schema 與來源證據；欄位範圍依 [核對規則](rules/barc-check-rules.md) §3。（Issue #7 第一階段；配息／提前出場表等列於報告「未涵蓋」）
- [x] 導入下單資料 adapter：依 `config/order_formats/<上手>.toml` 讀取上手原始格式（先做 BARC 詢價表）；測試用合成 Excel，不提交真實檔案。（Issue #7）
- [x] BARC 詢價表：MKI = Monthly KI、Period End 型保證配息期規則已確認。
- [ ] 收集更多 BARC 詢價表樣本（Period End、有 KI、日幣／人民幣、Monthly KI），驗證目前推得的規則（[核對規則](rules/barc-check-rules.md) §6.4）。
- [x] 版本化規則、逐欄結果、JSON 與人可讀例外報告；明示核對範圍與未支援欄位。（Issue #7）
- [x] README 補安裝、CLI 範例、輸入輸出及錯誤狀態；不得用未執行的指令冒充可用功能。（Issue #7）
- [x] 第二階段（Issue #9）：配息評價日／支付日表與自動提前出場表解析、保證配息期、日期規則 B／C 類、§16 情境分析價格表重印、最低申購／贖回金額。
- [x] 依 #38 範圍決定補齊文件內重複出現處、情境試算與審查標準固定值（Issue #41）。
- [ ] KI 每期觀察（P，即以前說的 Monthly KI）：BARC、HSBC 還沒有樣本，維持人工覆核。Issue #10 已於 2026-10-13 關閉（not planned），取得樣本後再重開。
- [x] 以參考條件表批量核對並回填 ISIN 與比價日（Issue #43，CLI `fcn-batch`）。
- [x] PANEL 改用參考條件表（含批量選檔），並刪除 BARC 詢價表程式與設定（Issue #46，ADR 0004）。
- [ ] BARC D 型第 1 期期始日就有日期（S08、S10 型）的比價日填法：待有已確認的參考條件表資料。

驗收：合成 PDF + 下單資料可走完整流程，欄位與證據正確；一致、差異、缺值、歧義、未知範本各有可觀察結果。未支援／不完整輸入不會產生整體 PASS。

## 2. 回歸資料與品質門檻

- [ ] 測試跨行標籤、換頁表格、多標的、重複值、旋轉頁及破損／加密 PDF。
- [ ] 測試百分比尺度、年率／每期率、Decimal 邊界、模糊日期及單位不一致。
- [ ] 測試必核欄位 coverage、重跑一致性與兩側來源證據完整度。
- [ ] 定義誤通過率、欄位精確度、覆核率及可接受門檻；使用未參與 parser 開發的保留樣本驗證。
- [ ] 增加 golden fixtures 與整合測試；CI 只使用合成或明確核准的去識別資料。

## 3. OCR 與更多範本

- [ ] 用樣本評估本地 OCR；逐頁處理掃描與混合文件，保存座標及品質訊號。
- [ ] OCR 低品質、數字混淆、漏頁直接轉覆核，測試不會靜默放行。
- [ ] 每新增 issuer／版本附 fixtures、偵測策略與支援欄位表；依[新增上手實作規範](issuer-onboarding.md)進行（第二家上手需先完成多上手架構調整，見規範 §7）。
  - [x] HSBC：規格與 H02 螢光範圍完成，Issue #34 實作 parser、整理表按商品代號選列、規則與 CLI／PANEL；SG 暫緩（Issue #27）。
  - [x] MS：說明書規格與 S06 螢光範圍完成（Issue #132），說明書實作（Issue #135）。
  - [x] MS 投資人須知：範本規格 `docs/templates/ms-zh-iis.md`、核對規則 §3 MS 欄（Issue #137，已實作；只支援新版範本）。
  - [x] BNP：說明書探勘、BN4 雙方螢光範圍與規格完成（Issue #189；[範本規格](templates/bnp-zh-product-description.md)、[核對規則](rules/bnp-check-rules.md)）。
  - [x] BNP 說明書實作（Issue #191）：parser、`issuers.py` 登記、審查標準 BNP 分節與 `{autocall_*}` 佔位符、最低金額倍數、保證機構名稱、分割調整轉人工（所有上手）。
  - [x] BNP 投資人須知（Issue #197）：探勘、範本規格與實作（[範本規格](templates/bnp-zh-iis.md)）；兩份都通過才回填。本機參考條件表沒有 BNP 的列，先以同商品說明書驗證；有列後補「兩份都通過才回填」的本機真實樣本斷言。
  - [x] SG：說明書探勘與規格完成（Issue #192；作業人員決定跳過雙方螢光取交集；[範本規格](templates/sg-zh-product-description.md)、[核對規則](rules/sg-check-rules.md)）。
  - [x] SG 說明書實作（Issue #194）：parser 含兩種版面、`issuers.py` 登記、審查標準 SG 分節（警語與 `[RRn]` 變體、`autocall_memory_zh`、保證機構與總代理人名稱、最低申購依幣別倍數、受託機構簡稱與地址等價寫法）。
  - [x] SG 投資人須知（Issue #213）：探勘、規格與實作完成（作業人員決定不畫螢光、逐項決定；警語七「受託投資」照現行規則判錯，所以現有 6 份樣本都不通過；[範本規格](templates/sg-zh-iis.md)、[核對規則](rules/iis-check-rules.md) §3 SG 欄）。本機真實樣本測試用含 SG 列的 `data/核對表格式..xlsx`，核對結果與回填都已斷言。
  - [x] Citi 說明書與投資人須知（Issue #220）：探勘、規格與實作完成（作業人員決定不畫螢光、逐項決定；Citi 版本固定警語、兩個「受託投資」允許片語、標的依代號配對、期初定價欄核對、非記憶式不得寫「記憶式」；[說明書範本規格](templates/citi-zh-product-description.md)、[投資人須知範本規格](templates/citi-zh-iis.md)、[核對規則](rules/citi-check-rules.md)）；本機 8 組樣本中說明書與投資人須知都通過的 5 組回填。
  - [ ] JPM 說明書與投資人須知（Issue #233）：探勘與規格完成（作業人員決定不畫螢光、逐項決定；JPM 版本固定警語、「受託投資/受託買賣」允許片語、標的依代號配對、期初定價欄不核對；[說明書範本規格](templates/jpm-zh-product-description.md)、[投資人須知範本規格](templates/jpm-zh-iis.md)、[核對規則](rules/jpm-check-rules.md)）；待實作。
  - [x] Nomura 說明書與投資人須知（Issue #239）：探勘、規格與實作完成（作業人員決定不畫螢光、逐項決定；每月配息率 × 12 = 年利率、固定警語 SG 版本說明書 2 次（新增上手別次數設定）、投資人須知沒有刊印日期、審查日期清單加 2025-06-24；[說明書範本規格](templates/nomura-zh-product-description.md)、[投資人須知範本規格](templates/nomura-zh-iis.md)、[核對規則](rules/nomura-check-rules.md)）；本機 8 組樣本中 7 組通過並回填，1 組因參考條件表到期日填錯判不一致。
  - [ ] UBS：樣本與參考條件表列已就位（2026-10-13），尚未探勘。
  - [x] 上手編號對照補齊尚無範本的 11 家（Issue #149）：未支援上手的錯訊寫出上手代號。
  - [x] 其他幣別：可承作的八種幣別（USD、JPY、CNH、HKD、AUD、EUR、GBP、ZAR）審查標準 `[currency]`、`[denomination]` 都有了；澳幣、歐元、英鎊、南非幣在有樣本確認中文寫法後於 2026-10-13 加入（Issue #185）。表裡沒有的寫法（例：澳元、南非蘭特）仍轉人工覆核。
  - [x] MS 第二章受託機構營業所在地 6 份樣本都少「松山區」：2026-10-09 確認視為正確，審查標準加地址等價寫法（[核對規則](rules/ms-check-rules.md) §5）。

## 4. 試行前

- [ ] 公司電腦上線驗收：依[上線驗收流程](acceptance.md)，BARC、HSBC、MS 每個條件至少 1 檔最新交易驗收通過（Issue #153）。

- [ ] 確認資料存取／保存／刪除政策、日誌遮蔽與輸出報告權限。
- [ ] 設計人工覆核紀錄，保留原始判定；確認報告供誰使用及放行責任。
- [ ] 決定批次錯誤隔離、失敗重跑與效能目標。（單筆 CLI 結束碼已定：0 PASS／1 不一致或需人工／2 ERROR）

## 第一版明確不做

LLM SDK／API／fallback、通用任意 PDF 理解、定價、下單執行、Web UI、資料庫、自動交易放行。未來 LLM 擴充須另立 ADR；目前只保留 extraction 邊界。
