# 0009: 上手 adapter 交出標準欄位，共用規則只實作一次、語意逐條定義

- Status: Accepted
- Date: 2026-10-15
- 取代 [ADR 0005](0005-issuer-adapter-and-shared-rules.md)

## Context

[ADR 0005](0005-issuer-adapter-and-shared-rules.md)（2026-10-02）在只有 BARC、HSBC 兩家時決定：
- 上手 adapter 只交出標準欄位與說明書內部規則。
- 參考條件表與審查標準規則各上手共用。
- 兩家語意不同時「以 BARC 為準」。

到 2026-10-15，14 家上手（BARC、HSBC、MS、BNP、SG、Citi、JPM、Nomura、GS、Natixis、BBVA、CA、UBS、DBS）都已導入，暫時沒有要新增的上手，接下來的工作是整併與優化現有程式。0005 有幾處已經和現況不符：

- **以 BARC 為準**：實際上已經逐條決定。月配息率依各上手說明書規則，不統一成 BARC 語意（ADR 0007 補充，Issue #137）。日期表結構檢查新訂名稱，不照 BARC（Issue #289）。各上手核對規則文件都連到 BARC 文件當共用規則的說明，共用規則的定義因此和 BARC 的專屬規則混在一起。
- **adapter 交出的東西**：寫「只提供四樣」，現在是六樣，另有 `reference_fields` 與投資人須知範本（ADR 0007）。
- **讀參考條件表**：寫成「唯一例外、需經審查才加」，現在 14 家有 7 家宣告讀年利率。
- **推算**：寫「月配息率推算仍是唯一留在上手內的推算」，現在 BNP、SG、MS、HSBC 的情境分析都在各家重算。
- **檔案位置**：提到的 `rules/common.py` 已不存在。
- **補充**：6 段補充混著長期決定、實作流程（預覽時讀出一次、核對設定、單份核對順序，現已寫在 `docs/architecture.md`）與單一上手的事項（MS Non-Call = 天期，現已寫在 MS 核對規則）。

本 ADR 寫下目前有效的決定，取代 0005。

## Decision

- **上手 adapter 交出六樣**（`src/fcn_checker/issuers.py` 的 `Issuer`）：
  1. 識別資料：上手代號、範本 ID、名稱、parser 版本、未涵蓋清單。
  2. 辨識 `detect`：文字行 → 是否為這家上手的範本（含證據）。
  3. 讀出 `read`：文字行 → 標準欄位（`standard_fields.STANDARD_FIELDS`），加上該上手規則需要的專屬資料。
  4. 說明書內部規則 `rules`：只收 `IssuerContext`。
  5. 規則可讀的參考條件表欄位 `reference_fields`。
  6. 投資人須知範本 `iis`（[ADR 0007](0007-iis-paired-with-term-sheet.md)）。
- **推導在讀出階段完成**：提前出場排程等從說明書推得的標準欄位，在 `read` 交出，因為預覽辨識（例：不支援樣態）就要讀到它們。多家相同的推導寫成共用函式，由 adapter 呼叫（例：Issue #289 的排程推導）；共用推導與標準欄位的形狀是同一份契約。
- **範本沒有與沒交出分開**：範本本身沒有的欄位交出 `absent`（不適用），共用規則不比對、出處清單不產生結果。該交卻沒交出的欄位是 `not_provided`，相關規則轉人工覆核並寫出欄位名稱。
- **共用規則只實作一次，語意逐條定義**：
  - 共用規則有：參考條件表欄位與 Non-Call（`rules/reference.py`）、說明書推算（`rules/derivation.py`）、日期表結構（`rules/schedule.py`，Issue #289）、審查標準（`rules/review_standard.py`）、回填（`backfill.py`）、投資人須知（`rules/iis.py`）。
  - 每條規則的語意由[共用核對規則](../rules/shared-check-rules.md)（投資人須知見 [iis-check-rules.md](../rules/iis-check-rules.md)、審查標準見 [review-standard.md](../rules/review-standard.md)）定義，不以任何一家上手為準。
  - 範本本身的寫法或條款定義不同時，由 adapter 宣告（`absent`、`reference_fields`、交出的值），或放在設定檔的上手分節（`config/review_standard.toml`、`config/reference_sheet.toml`）。
  - 共用規則不判斷上手代號。
  - 改共用規則的語意，就是改那一條的定義：同步更新共用核對規則文件，對 14 家一起生效。
- **上手說明書內部規則只拿 `IssuerContext`**，內容是：
  - 讀出結果、審查標準、上手代號。
  - 交易所假日表：提前出場觀察期的「一籃子預定交易日」是說明書條款本身的定義，不是交易資料（Issue #208）。
  - 該上手宣告的參考條件表欄位。

  讀未宣告的欄位是開發期錯誤。只有說明書印的值是由參考條件表才有的條件推算出來時才宣告（例：月配息率 = 年利率 ÷ 12）。

## Alternatives considered

- **每家上手各寫一份參考條件表規則**：同一欄位的結論會因上手而不同，改一條規則要改 14 處。
- **共用規則加上手開關參數**：每多一個差異，每條共用規則都要再加參數，差異散在各處。
- **上手註冊項目保留接線欄位（`isin`、`autocall_schedule`、`context` 等）**：介面寬、型別為 `Any`，推導邏輯散在規則端。
- **兩家不同時以某一家（BARC）為準**：0005 的做法。14 家的條款寫法真的不同（月配息率的捨入、期始日的定義、有沒有 KO 欄），硬套一家會把範本差異變成誤報；共用規則的說明也被綁在 BARC 的文件裡。
- **年利率、天期一律給上手規則，不必宣告**：少一個機制，但上手規則和參考條件表的邊界會變模糊，看不出哪家規則依賴表上的值。
- **推導搬到共用規則端**：adapter 更薄，但規則在核對時才跑，預覽辨識拿不到排程。

## Consequences

- 共用規則的定義集中在[共用核對規則](../rules/shared-check-rules.md)；BARC 核對規則只保留 BARC 專屬規則與當初的決策紀錄，其他上手的核對規則文件只寫該上手的差異。
- 標準欄位清單、共用推導函式與共用規則文件是 adapter 與共用規則之間的契約：新增共用規則需要的欄位時，同步更新 `STANDARD_FIELDS`、各上手的 `read` 與共用規則文件。
- 預覽與核對怎麼分段、核對設定、單份核對的順序屬於實作，記在 [docs/architecture.md](../architecture.md)；單一上手的事項（例：MS Non-Call = 天期時 KO 視為 100%，Issue #202）記在該上手的核對規則文件。
- 新上手的步驟仍見[新增上手實作規範](../issuer-onboarding.md) §6；目前沒有預定要新增的上手。
