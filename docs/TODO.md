# TODO 與開工清單

## 0. 明天先確定範圍

- [x] 選一家 issuer、一個範本版本；確認樣本為文字／掃描／混合 PDF。→ 巴克萊（BARC）中文產品說明書，14 份樣本皆為文字型 PDF。見 [BARC 範本規格](templates/barc-zh-product-description.md)。
- [ ] 取得可合法使用且去識別的樣本，建立人工標註的 expected output；真實樣本留在 Git 外。（真實樣本已放本機 `data/ts/`；去識別 fixture 尚未建立）
- [x] 確認權威下單來源（CSV、Excel、系統匯出或其他）與交易配對方式。→ 各家上手原始格式；BARC 為詢價表，以 B3 商品代號配對。見 [BARC 詢價格式](order-formats/barc-inquiry.md)。（原 `FCN參考條件.xlsx` 已停用）
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
- [ ] Monthly KI（Issue #10，待樣本，暫時維持人工覆核）。

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
  - [ ] HSBC：規格完成（Issue #32），待多上手架構（Issue #28）合併後開實作 Issue；SG 暫緩（Issue #27）。

## 4. 試行前

- [ ] 確認資料存取／保存／刪除政策、日誌遮蔽與輸出報告權限。
- [ ] 設計人工覆核紀錄，保留原始判定；確認報告供誰使用及放行責任。
- [ ] 決定批次錯誤隔離、失敗重跑與效能目標。（單筆 CLI 結束碼已定：0 PASS／1 不一致或需人工／2 ERROR）

## 第一版明確不做

LLM SDK／API／fallback、通用任意 PDF 理解、定價、下單執行、Web UI、資料庫、自動交易放行。未來 LLM 擴充須另立 ADR；目前只保留 extraction 邊界。
