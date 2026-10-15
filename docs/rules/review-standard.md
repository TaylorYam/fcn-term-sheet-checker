# 審查標準（Review Standard）

- 設定檔：[`config/review_standard.toml`](../../config/review_standard.toml)
- 狀態：2026-10-01 與使用者確認；已實作為 `standard.*` 規則（Issue #7），並在本機以 14 份 BARC 樣本驗證（結果同 §3）。HSBC 差異（§2.2、§3.1）已確認並實作（Issue #32、#34）；MS 差異（§2.6）已確認並實作（Issue #132、#135）；BNP 差異（§2.7）已確認並實作（Issue #189、#191）；SG 差異（§2.8）已確認並實作（Issue #192、#194）

## 1. 為什麼要獨立成設定檔

有些核對基準不是來自單筆交易，而是**受託機構的審查結果或公司規定**，會隨時間改變（例如受託機構重新審查後，審查通過日期就會更新）。這些基準集中放在 `config/review_standard.toml`：

- 基準改變時，只修改設定檔，不改程式；
- 每次修改要更新 `version` 與 `effective_date`，並透過 PR 留下變更原因；
- 設定檔只放公開的條款文字與日期，不放客戶或交易資料。

核對開始時先載入審查標準；報告要記錄本次使用的審查標準版本。

## 2. 內容與規則

| 設定 | 目前值 | 檢查位置 | 規則 |
|---|---|---|---|
| `distributor.approval_dates` | 2025-12-18、2026-06-11（歷次日期，由舊到新） | 封面「受託或銷售機構審查通過之日期」 | 等於清單中「交易日當天或之前最近一次」的日期（§2.5） |
| `distributor.chairman` | 林晋輝 | 第二章「受託或銷售機構」→「負責人姓名」 | 逐字相等（含字碼）；「晋」是古字 U+664B，寫成「晉」U+6649 即為錯誤 |
| `distributor.name`／`phone`／`address` | 玉山綜合證券股份有限公司／02-5556-1313／台北市松山區民生東路三段158號6樓 | 封面「受託或銷售機構之名稱、電話及地址」；第二章「受託或銷售機構」→「事業名稱」「營業所在地」 | 忽略空白與換行後逐字相等（2026-10-02，Issue #41） |
| `distributor.phone_equivalents` | `+886-2-5556-1313`（國際寫法，HSBC 封面使用）、`+886 2 5556 1313`（MS 封面使用） | 封面受託或銷售機構電話 | 電話除 `phone` 外，忽略空白後等於清單中任一寫法也算相符；**所有上手適用**；清單沒列的寫法判定不一致（2026-10-02，Issue #52）。要接受新的寫法時在清單加一筆，不改程式 |
| `distributor.address_equivalents` | `台北市民生東路三段158號6樓`（沒有「松山區」，MS 第二章使用） | 封面與第二章受託或銷售機構地址（投資人須知各處地址同） | 用法同 `phone_equivalents`：忽略空白後等於清單中任一寫法也算相符；**所有上手適用**（2026-10-09，Issue #135） |
| `issuer_name.<上手>` | BARC：英商巴克萊銀行股份有限公司（Barclays Bank PLC） | 封面「發行機構」；第二章「發行機構」→「事業名稱」；範本有只寫中文的出處時（MS 第一章 §3）只比中文 | 忽略空白與換行後逐字相等；缺該上手分節時轉人工覆核 |
| `issuer_name_ignore.<上手>` | MS：`brackets`、`trailing_period` | 同上 | 該上手比對時另外忽略括號（全形／半形）、英文名結尾的句點；沒列出的上手不忽略 |
| `fees` | 申購費用、提前贖回費用、分銷費用皆為 0%~5% | 第四章「投資人應負擔的各項費用」表的費率欄 | 忽略空白後逐字相等；只核對列出的費用項目 |
| `issue_price.pct` | 100 | 第一章 §6「發行價格為商品面額之N%」；範本另有出處時每處一筆（MS 第四章 §5 申購價金） | 相等；不相等時**轉人工覆核**（不直接判錯） |
| `risk.level` | RR4 | 全文所有【RRn】（寫法不同的上手見 `risk.level_formats`） | 全部等於設定值 |
| `risk.level_formats.<上手>` | MS：`風險程度等級為{level}`、`風險程度為{level}`、`歸類為{level}` | 全文 | 該上手的風險等級寫法（`{level}` 為 RRn）；沒列出的上手為【RRn】 |
| `risk.fixed_warning` | 固定風險警語全文（我方提供給上手） | 全文 | 忽略空白與換行後逐字相等，且出現次數 = `fixed_warning_occurrences`（BARC 為 3 次：封面警語 1、第一章 §2、第三章投資風險警語） |
| `risk.fixed_warning_occurrences_by_issuer.<上手>` | Nomura 2（封面(一)、第三章 6.(1)；2026-10-15，Issue #239） | 說明書全文 | 該上手說明書的固定警語次數；沒列出的上手用 `fixed_warning_occurrences`（3） |
| `risk.fixed_warning_by_issuer.<上手>` | 依上手不同的固定警語版本（`hsbc`、`ms`） | 同上 | 有該上手版本時改用該版本比對；沒有時用 `risk.fixed_warning` |
| `risk.fixed_warning_openings.<上手>` | MS：`本商品風險程度為RR4。` | 同上 | 開頭句（到第一個「。」）的其他允許寫法；只換開頭句、其餘逐字相同的版本也算一次 |
| `wording.forbidden` ／ `allowed_phrases` | 禁用「受託投資」；允許「受託投資或受託買賣」「受託投資、受託買賣或為投資型保單」（HSBC）、「受託投資、受託買賣之投資標的」「受託投資或銷售本商品」（Citi，2026-10-14 確認，Issue #220） | 全文 | 片語以外出現「受託投資」即為錯誤（SOP：須改為「受託買賣」） |
| `product_name.<上手>.zh` ／ `en` | 名稱樣板（依上手分節，例如 `product_name.barc`；缺該上手分節時轉人工覆核） | 封面「商品中文名稱」「商品英文名稱」 | 用說明書欄位填佔位符組出預期名稱後逐字比對（§2.3）；全形／半形括號差異不算錯 |
| `guarantor_name.<上手>` | BNP：`cover`、`ch1`、`ch2` 三處各一個寫法 | 封面「保證機構之名稱及地址」、第一章「保證機構：」、第二章「保證機構」事業名稱；BNP 投資人須知寫全名處（相關機構 3）用 `cover`、寫簡稱處（警語 5 兩處、投資風險「信用風險」）用 `ch1`（2026-10-14，Issue #197） | 忽略空白後逐字相等；沒有該上手分節的上手不核對（沒有保證機構） |
| `guarantor_name_equivalents.<上手>` | SG：`法商法國興業銀行股份有限公司Societe Generale`（投資人須知相關機構壹 2.，2026-10-14，Issue #213） | 投資人須知相關機構的保證機構名稱（只有這一處；說明書與投資人須知警語仍用 `guarantor_name` 的固定值） | 除 `cover` 的固定值外，忽略空白後與其中之一相等也視為相符 |
| `agent_name.<上手>` | SG：`cover`、`ch2` | 封面「總代理人」、第二章總代理人事業名稱；投資人須知相關機構壹 3.（用 `cover`） | 忽略空白後逐字相等；沒有該上手分節的上手不核對 |
| `product_type.<上手>` | BNP：`股權連結結構型商品` | 封面「商品種類」 | 忽略空白後逐字相等；沒有設定的上手不核對（MS 依標的數由上手規則核對） |
| `minimum_issue_size.<上手>` | BNP：`(等值)300,000 美元` | 第四章「發行不成立之情形」的最低有效申購金額 | 忽略空白後逐字相等；沒有設定的上手不核對 |
| `min_amount_multiples.<上手>` | BNP：最低申購、最低贖回 5，最低加購、增加贖回單位 1 | 第四章各最低金額（`field.min_amounts`） | 各出處 = 參考條件表單位面額 × 倍數；沒列的出處倍數為 1（BARC、HSBC、MS 不變） |
| `currency` | 美元→USD、日幣／日圓→JPY、人民幣／境外人民幣→CNH、港幣／港元→HKD、澳幣→AUD、歐元→EUR、英鎊→GBP、南非幣→ZAR（樣本看過的寫法，加上使用者同意的同義寫法）。表裡沒有的寫法（例：澳元、南非蘭特）未確認前不放：封面幣別轉人工覆核一次，其他出處寫同一個字時不重複報（2026-10-10 使用者決定；AUD、EUR、GBP、ZAR 有樣本後於 2026-10-13 加入，Issue #185） | 封面計價幣別；金額旁的幣別字（§2.4 `field.currency` 其他出處） | 不在表內的幣別轉人工覆核；金額旁的幣別字 parser 認得的寫法在 `src/fcn_checker/parsers/money.py`，新幣別兩邊都要加 |
| `denomination` | USD／AUD／EUR／GBP 10,000；JPY 1,000,000；CNH／HKD／ZAR 100,000（2026-10-10 使用者提供的承作幣別表） | §6 商品面額 | 等於該幣別預設值；不等於時**轉人工審查**（客戶可要求不同面額，不直接判錯） |
| `iis.pages` | 4 | 投資人須知（Issue #124） | 頁數剛好等於設定值（比外規「不超過 5 頁」嚴格） |
| `iis.fixed_warning_occurrences.<上手>` | BARC 1、HSBC 2、MS 1（警語 1)，2026-10-09，Issue #137）、BNP 2（警語 1 與商品簡介「商品風險程度」，2026-10-14，Issue #197）、SG 1（警語一，2026-10-14，Issue #213）、Citi 1（警語 1.，2026-10-14，Issue #220）、Nomura 1（警語(一)，2026-10-15，Issue #239） | 投資人須知全文 | 固定警語（依上手版本）出現次數；沒列出的上手轉人工覆核 |

### 2.1 商品名稱樣板（BARC，依最新 5 份說明書）

- 中文：`英商巴克萊銀行{天期}個月{幣別中文}計價連結股權{記憶式}自動提前出場結構型商品（不保本）（無擔保及無保證機構）（下稱「本商品」）`
- 英文：`{天期} Months {幣別代碼} {Memory }Autocallable Equity Linked Note issued by Barclays Bank PLC (unsecured and non-guaranteed)`
- `{記憶式}`／`{Memory }` 只在記憶式商品出現。
- 中文名稱必須包含「（不保本）」；英文名稱不加 principal protection 相關字樣。

### 2.2 HSBC 的差異（2026-10-01 確認，Issue #32）

審查通過日期、負責人、風險等級、刊印日期規則、幣別與面額預設值與 BARC 相同。不同的只有：

- **固定警語**：HSBC 版本把前 4 處「受託機構」寫成「受託或銷售機構」，第 5 處「依受託機構審核」不變，其餘逐字相同；視為正確，存於 `risk.fixed_warning_by_issuer.hsbc`。同樣出現 3 次（封面警語 1、第一章 §2、第三章）。
- **允許片語**：HSBC 封面警語 7 與第三章使用法規列舉寫法「於臺灣境內受託投資、受託買賣或為投資型保單之投資標的」，片語「受託投資、受託買賣或為投資型保單」加入 `allowed_phrases`（對所有上手適用）。
- **商品名稱樣板**（`product_name.hsbc`，依 8 份樣本推得）：
  - 中文：`香港上海滙豐銀行{天期}個月{幣別中文}計價{記憶式}自動提前到期固定配息股權連結（無保證機構無擔保）（不保本）結構型商品（以下簡稱「本商品」）`
  - 英文：`{Maxi }{Daily }{Memory }Autocallable Fixed Coupon Notes (Non Guaranteed, Unsecured)`；`Maxi ` 標的數 ≥ 2、`Daily ` 提前出場每日觀察、`Memory ` 記憶式。
  - 英文名稱在 PDF 換行處常黏字，比對時忽略所有空白（`ignore_whitespace_en`）；中文括號全半形不計。

### 2.3 依上手解析與商品名稱佔位符（Issue #52）

核對每份說明書時，先依辨識出的上手代號取出「該上手適用的審查標準」，共用規則只用這份已解析的標準，不再各自判斷上手：

- 固定警語：有 `risk.fixed_warning_by_issuer.<上手>` 就用，否則用 `risk.fixed_warning`。
- 商品名稱樣板 `product_name.<上手>`、發行機構全名 `issuer_name.<上手>`：缺該上手的值時轉人工覆核（`standard_missing`）。
- 受託機構名稱、電話（含 `phone_equivalents`）、地址（含 `address_equivalents`）：所有上手共用。

商品名稱樣板的佔位符一律由說明書欄位填入，不依上手分支：

| 佔位符 | 填入內容 |
|---|---|
| `{tenor}` | 天期月數 |
| `{ccy_zh}`／`{ccy}` | 幣別中文／ISO 代碼 |
| `{memory_zh}`／`{memory_en}` | 記憶式時填入該上手樣板的 `memory_zh`／`memory_en`，否則空白 |
| `{maxi_zh}`／`{maxi_en}` | 標的數 ≥ 2 時填入 `maxi_zh`／`maxi_en`，否則空白 |
| `{daily_zh}`／`{daily_en}` | 提前出場每日觀察（KO D 型）時填入 `daily_zh`／`daily_en`，否則空白 |
| `{underlying_zh}`／`{underlying_en}` | 標的寫法：標的 1 檔填 `single_zh`／`single_en`、2 檔以上填 `basket_zh`／`basket_en`（兩組不同字串，不是「多檔才加前綴」；Issue #135） |
| `{autocall_zh}`／`{autocall_en}` | 第一個可提前出場期早於到期（Non-Call < 天期）時填入 `autocall_zh`／`autocall_en`，否則空白（BNP，Issue #191）；會提前出場且記憶式、樣板另有 `autocall_memory_zh`／`autocall_memory_en` 時改填它（SG 中文「（記憶式自動提前出場）」，Issue #194） |
| `{ccy}` 的別名 | 樣板的 `ccy_aliases`（ISO 代碼 → 名稱用的代碼）：SG 英文名稱把 CNH 寫成 CNY（Issue #194） |

樣板出現上表以外的佔位符，或用了 `{underlying_*}` 卻沒設定 `single_*`／`basket_*` 時，載入審查標準就回報設定錯誤（`config_invalid`，整批不核對），不會在核對途中才出錯。`risk.level_formats`、`risk.fixed_warning_openings`、`issuer_name_ignore` 寫法有誤時同樣在載入時回報。

### 2.4 各上手共用的出處規則（Issue #55）

下列規則在 `rules/review_standard.py` 只實作一次，所有上手共用，語意與 rule_id 採 BARC 版本。各上手只用標準欄位交出該規則涵蓋的出處清單（`standard_fields.Occurrence`），每處出處各產生一筆結果：

| rule_id | 規則 | 出處清單（標準欄位） |
|---|---|---|
| `doc.denomination` | 面額 = 該幣別的 `denomination` 預設值，不同時轉人工覆核（`denomination_non_default`） | `denomination` |
| `field.min_amounts` | 各最低金額 = 參考條件表「單位面額」× 該出處的倍數（`min_amount_multiples.<上手>`，沒設定為 1；讀參考條件表，所以放在 `rules/reference.py`；2026-10-02 使用者決定、2026-10-13 加倍數） | `min_amounts`：BARC 最低申購、最低贖回；HSBC 最低交易、最低申購、最低加購；MS 第四章四個；BNP 第四章四個（申購、贖回 5 倍） |
| `field.currency`（其他出處） | 金額旁的幣別字與承作幣別的其他出處 = 參考條件表「承作幣別」（中文幣別依 `currency` 對照；寫法不在對照表時只由封面那筆報，和封面寫同一個字的出處不重複）；表上承作幣別空白或格式不對時只由封面那筆報。**商品幣別不必等於標的幣別**（FCN 高度客製化，例：連結日股、美元計價；連結美股、人民幣計價，2026-10-10 使用者決定，Issue #170）：承作幣別只影響面額、配息、本金這類金額，執行價、收盤價、觸及不保本價格等股價跟著標的走，股價與含「股」的實物交割算式所在的行整行不列入出處（同一行的商品幣別金額也不核對）。關鍵字落在上一行（算式說明在上一行、數字在下一行）或被換行拆開（`收盤價`／`格小於…`）時，看同一句裡金額前面的文字（到上一個「。，；：」或上一個算式結束為止），同樣不列入，但算式的結果（`=` 後面、後面不再接運算，例：以承作幣別支付的剩餘金額、到期贖回價值）仍列入，同一句裡不在 `=` 後面的金額不列入（已知限制：一句跨多行都沒有標點、前面又出現股價字眼時，後面的配息、本金也不列入；現有樣本都有標點，2026-10-12 使用者決定暫不收緊）；匯率寫法（`1 日圓 = 美元 0.006`）是兩種幣別的換算，不列入（2026-10-12，Issue #179）；情境試算每個情境一筆，列出該情境裡幣別不符的金額。parser 只認得 `parsers/money.py` 列的幣別寫法，寫法不在清單時那一處是「抓不到」轉人工覆核 | `currency_others`：BARC §6 面額、第四章最低申購／最低贖回金額、§16 情境假設面額、§16 (i)～(iii) 各情境；MS 第一章 §6 面額、第四章 §4、§8 四個最低金額、§18 假設總投資金額、§18 各情境（較差情境的收盤價、執行價不算）；HSBC §6 面額、§7 最低交易金額、第四章最低申購／加購金額、§18 情境假設與各情境（執行價、觸及不保本價格不算；§12 價格表幣別格跟著標的走，不核對，取代 Issue #167）；投資人須知：BARC 每單位商品面額、最低申購金額，HSBC 每單位面額，BNP 商品面額、最低申購金額 |
| `doc.subscription_start_date` | 受理申購日 ≤ 交易日（2026-10-15，#254） | `subscription_dates`：BARC 開始受理日；HSBC 開始受理日、申購結束受理日 |
| `doc.print_date` | 交易日（含）≤ 刊印日期 < 發行日（不含）；發行日取同一份文件（2026-10-15，#254） | `print_dates`：BARC 一個；HSBC 參考性審閱版、最終版 |

### 2.5 審查通過日期依交易日核對（Issue #120）

受託或銷售機構約每半年重新審查一次（時間不固定），每次產生新的審查通過日期；歷次日期都保留在 `distributor.approval_dates`。

- 預期值 = 清單中交易日當天或之前最近一次的日期；說明書日期不同即判不一致。交易日在新日期之後仍印舊日期也判不一致，不設寬限期（上手沿用舊範本正是要抓出的情況；確認可接受時用人工放行）。
- 讀不到交易日（`trade_date_unavailable`），或交易日早於清單中最早的日期（`approval_date_not_configured`）：轉人工覆核。
- 清單至少一筆、只能放日期、不能重複，否則設定檔載入失敗。
- 維護：在 PANEL「審查通過日期…」新增晚於最新一筆的日期，或修改／刪除最新一筆；較早的日期不能在 PANEL 修改。存檔時自動 `version` + 1、`effective_date` 改為當天，其餘內容與註解不動。改完提交到 main。
- 不做審查到期提醒（重新審查時間不固定）。固定風險警語與商品名稱樣板只保留目前版本，不依交易日保留歷史。

### 2.6 MS 的差異（2026-10-08 確認，Issue #132；Issue #135 實作）

細節見 [MS 核對規則](ms-check-rules.md) §3.2、§3.6。審查通過日期、負責人、風險等級、受託機構名稱與地址、費率、發行價格、面額預設值、刊印日期規則與 BARC 相同。不同的有：

- **固定警語**（`risk.fixed_warning_by_issuer.ms`）：現行版本去掉【】，結尾沒有句號；第一章 §2 開頭寫成「本商品風險程度為RR4。」（少「等級」）也視為正確（`risk.fixed_warning_openings.ms`）。出現 3 次。
- **風險等級**（`risk.level_formats.ms`）：沒有【】，以「風險程度等級為RRn」「風險程度為RRn」「歸類為RRn」找出每一處。
- **商品名稱樣板**（`product_name.ms`）：中文 `英商摩根士丹利發行{天期}個月期{幣別中文}計價連結{標的寫法}固定配息{記憶式}結構型商品(無保證機構)(無擔保)(不保本)(下稱「本商品」)`，英文 `Morgan Stanley & Co. International Plc issuance of {天期} Months {幣別代碼} Fixed Coupon Notes linked to {標的寫法} (non-guaranteed)(unsecured)(non-principal protected)`。標的寫法依 1 檔／多檔是兩組不同字串，用佔位符 `{underlying_zh}`／`{underlying_en}`（§2.3）。
- **發行機構全名**（`issuer_name.ms`）：英商摩根士丹利國際股份有限公司(Morgan Stanley & Co. International Plc.)；第二章寫法沒有括號與英文結尾句點，比對時忽略（`issuer_name_ignore.ms`）；第一章 §3 只寫中文，只比中文。
- **受託機構電話**：封面寫 `+886 2 5556 1313`，`phone_equivalents` 加這個寫法。
- **受託機構地址**：第二章「◎ 營業所在地」寫 `台北市民生東路三段158號6樓`（少「松山區」），2026-10-09 確認視為正確，`address_equivalents` 加這個寫法。
- **受理申購日**：MS 範本沒有；改由 MS 專屬規則核對開始受理贖回日期。
- 審查通過日期只以 2026 年後的文件為準，不為 2025 年的舊審查日期加清單。

### 2.7 BNP 的差異（2026-10-13 確認，Issue #189；Issue #191 實作）

細節見 [BNP 核對規則](bnp-check-rules.md) §3.2、§3.6。審查通過日期（含月份補零的寫法）、負責人、風險等級值、受託機構名稱、費率、發行價格、面額預設值、刊印日期規則與 BARC 相同。不同的有：

- **固定警語**（`risk.fixed_warning_by_issuer.bnp`）：現行版本的 `【RR4】` 改為 `[RR4]`（半形方括號）、`【證券交易所交易證券】` 去掉【】、沒有 `*`；結尾有句號。出現 3 次（封面警語 (1)、第一章 §2、第三章 §6(1)）。固定警語只算 (1) 段，(2)～(9) 不核對。
- **風險等級**（`risk.level_formats.bnp`）：`風險程度等級為[RRn]`、`歸類為[RRn]`。
- **商品名稱樣板**（`product_name.bnp`）：中文 `法國巴黎銀行{天期}個月期{幣別中文}計價固定配息{自動提前贖回}連結{單一|一籃子}股票/交易所買賣基金結構型商品(不保本)(無擔保)`，英文 `{天期} Months {幣別代碼} Fixed Coupon {Early Redeemable }Note linked to {a Single Share/ETI|a Basket of Shares/ETIs} issued by BNP Paribas Issuance B.V. (unsecured)`。標的寫法用 `{underlying_zh}`／`{underlying_en}`；「自動提前贖回／Early Redeemable」只在 Non-Call < 天期時出現，用佔位符 `{autocall_zh}`／`{autocall_en}`（§2.3）。沒有記憶式字樣。
- **發行機構全名**（`issuer_name.bnp`）：`BNP Paribas Issuance B.V.`（只有英文法人名）；封面 5、第一章 §3、第二章 §1(1) 三處寫法相同。
- **保證機構名稱**（BNP 特有，新增 `guarantor_name.bnp`）：封面 11 `法商法國巴黎銀行股份有限公司 (BNP Paribas)`、第一章 §3 `法國巴黎銀行`、第二章 §2(1) `法國巴黎銀行 (BNP Paribas)`，三處各自固定；地址不核對。
- **商品種類**：封面 4 固定 `股權連結結構型商品`，不隨標的數改變（MS 依標的數）。
- **最低金額倍數**（BNP 專屬，2026-10-13 使用者確認）：最低申購金額、最低贖回金額 = 5 × 單位面額；最低加購金額、增加贖回單位 = 單位面額（`min_amount_multiples.bnp`，共用 `field.min_amounts` 依出處乘上倍數）。
- **發行不成立門檻**：第四章 §7 `(等值)300,000 美元` 為固定值（`minimum_issue_size.bnp`，2026-10-13 確認納入）。
- **保證機構名稱**與**商品種類**分別放在 `guarantor_name.bnp`、`product_type.bnp`（上表）。
- **受託機構電話**：封面與第四章 §16 寫 `+886 (2) 5556 1313`，`phone_equivalents` 加這個寫法。
- **受託機構地址**：封面與第二章寫 `臺北市105 民生東路三段158 號6 樓`（「臺」、郵遞區號、沒有「松山區」），`address_equivalents` 加 `臺北市105民生東路三段158號6樓`。
- **幣別對照**：加 `英鎊 = GBP`（BNP 樣本已確認中文寫法）。
- 總代理人、受託機構設立日期、基本公開說明書日期（只做文件內一致）不核對。

### 2.8 SG 的差異（2026-10-13 確認，Issue #192；已實作 Issue #194）

細節見 [SG 核對規則](sg-check-rules.md) §3.2、§3.6。實作新增的設定鍵：`risk.fixed_warning_variants.<上手>`（固定警語的其他完整寫法，每處都算一次）、`agent_name.<上手>`（總代理人名稱，出處 cover／ch2）、`distributor.name_equivalents`（受託機構名稱等價寫法）、`min_amount_multiples.<上手>.<出處>` 可寫成依幣別的表、`product_name.<上手>` 的 `autocall_memory_zh`／`autocall_memory_en` 與 `ccy_aliases`。審查通過日期（含月份補零的寫法）、負責人、風險等級值、受託機構電話、費率、發行價格、面額預設值、刊印日期規則與 BARC 相同。不同的有：

- **固定警語**（`risk.fixed_warning_by_issuer.sg`）：= MS 版本（無【】、有 `*`）加結尾句號。出現 3 次（封面警語一、第一章 §2、第三章 §5 一）。第一章 §2 的風險等級寫 `[RR4]`（半形方括號），封面與第三章寫 `RR4`：比對前把方括號寫法正規化，不得因此判不一致。固定警語只算警語一段，二～十不核對；警語七「為於／於中華民國境內受託買賣」兩種都允許。
- **風險等級**（`risk.level_formats.sg`）：`風險程度等級為RRn`、`歸類為RRn`、`風險程度等級為[RRn]`、`歸類為[RRn]`。
- **商品名稱樣板**（`product_name.sg`）：中文 `SG ISSUER {幣別中文}計價{天期}個月固定配息{autocall_zh}股權連結結構型商品（不保本及無擔保）`，英文 `SG ISSUER {幣別代碼} {天期}-month {Memory }{Autocallable }Fixed Coupon Equity Linked Notes (Non Principal Guaranteed and Unsecured)`。`{autocall_zh}` 為三值：記憶式 `（記憶式自動提前出場）`、非記憶式有自動提前出場 `可自動提前出場`、無自動提前出場空白（BNP `{autocall_*}` 佔位符的延伸，實作時加入 §2.3 表）；英文 `{memory_en}` = `Memory `、`{autocall_en}` = `Autocallable `。沒有單一／一籃子字樣。幣別代碼 CNH 在英文名稱寫 `CNY`。比對時中文忽略空白與全形括號。
- **發行機構全名**（`issuer_name.sg`）：`SG ISSUER`（只有英文法人名）；封面與第二章 §1(1) 兩處相同，第一章沒有出處（`issuer_name_ch1` 交 `absent`）。
- **保證機構名稱**（SG 特有，`guarantor_name.sg`，一個值）：封面、第一章 §3、第二章 §2(1) 皆 `法商法國興業銀行股份有限公司(Societe Generale)`；地址電話不核對。
- **總代理人名稱**（SG 特有，`agent_name.sg`）：封面、第二章 §3(1) 皆 `法商法國興業銀行股份有限公司台北分行`；地址電話、負責人不核對。
- **商品種類**：封面固定 `股權連結結構型債券`（寫「債券」；不隨標的數改變）。
- **最低申購金額倍數**（SG 專屬，依幣別，2026-10-13 使用者確認）：USD 10 × 單位面額、JPY／CNH 1 × 單位面額（其他幣別沒有樣本，轉人工覆核）；最低加購金額、最低贖回金額 = 單位面額。
- **受託機構名稱**：第二章 §3(4) 寫簡稱 `玉山證券` → 加名稱等價寫法。
- **受託機構地址**：封面寫 `台北市松山區民生東路3段158號6樓`（阿拉伯數字「3段」）→ `address_equivalents` 加入；第二章寫 `三段` 與現行值相同。
- **配息率**：= 年利率 ÷ 12，固定四捨五入 4 位（JPY 也 4 位）；情境配息金額 2 位。
- 發行不成立門檻、發行機構／總代理人／保證機構地址電話、基本公開說明書日期、參考條件表「期初定價」欄不核對。
- **投資人須知**（2026-10-14 確認並實作，Issue #213）：固定警語只在警語一出現 1 次（`iis.fixed_warning_occurrences.sg = 1`）；風險等級沒有 `[RRn]` 寫法；保證機構名稱在警語五 = `guarantor_name.sg`，相關機構壹 2. 寫 `法商法國興業銀行股份有限公司Societe Generale`（英文名沒有括號）視為等價寫法；總代理人只核對相關機構壹 3. 全名；受託機構簡稱 `玉山證券`、地址「3段」沿用上面的等價寫法。細節見[投資人須知核對規則](iis-check-rules.md) §3。

### 2.9 Citi 的差異（2026-10-14 確認，Issue #220；已實作）

細節見 [Citi 核對規則](citi-check-rules.md) §3.2、§3.6。審查通過日期、負責人、風險等級（預設【RRn】寫法）、受託機構名稱與地址、費率、發行價格、面額預設值、刊印日期規則與 BARC 相同。不同的有：

- **固定警語**（`risk.fixed_warning_by_issuer.citi`）：Citi 商品 **OSU 客戶不能承作**，Citi 版本沒有 OSU 客戶段，屬特例、視為正確（2026-10-14 確認）。其餘差異：`【證券交易所交易證券】` 無【】、`在綜合評量` 寫 `再綜合評量`、`受託機構受託買賣…審查管理辦法` 寫 `受託機構或銷售機構受託買賣…`、`受託機構將風險等級區分`／`由受託機構釐定` 寫 `受託或銷售機構`；結尾 `…投資本商品之專業投資人(專業投資人以下簡稱「投資人」)。`。說明書出現 3 次（封面 1.、第一章 §2、第三章 §4(1)）；投資人須知 1 次（`iis.fixed_warning_occurrences.citi = 1`）。
- **禁用語允許片語**（`wording.allowed_phrases`，所有上手生效）：加入「受託投資、受託買賣之投資標的」（警語 7.）、「受託投資或銷售本商品」（投資人須知警語 9.）。片語刻意寫窄：未支援的 CA 樣本有「受託投資或銷售時」「受託投資或銷售或OSU」，不連帶放過；已支援上手的樣本都沒有這兩種寫法，結果不變。
- **商品名稱樣板**（`product_name.citi`）：中文 `CGMHI {天期} 個月{幣別中文}計價{autocall_zh}連結{underlying_zh}並由花旗集團保證之固定配息結構型商品(無擔保) (不保本)（下稱「本商品」）`，`autocall_memory_zh = "(記憶式自動提前出場)"`、`autocall_zh = "(自動提前出場)"`（無自動提前出場空白）、`single_zh = "單一連結標的"`、`basket_zh = "一籃子連結標的中表現最差"`；英文 `{天期}-Month {幣別代碼} Fixed Coupon {autocall_en}Note linked to {underlying_en} issued by Citigroup Global Markets Holdings Inc. and guaranteed by Citigroup Inc. (Unsecured) (Non-principal protected)`，`autocall_en = "Autocall "`（記憶式同）、`single_en = "Single Underlying"`、`basket_en = "the Worst Performing Underlying in the basket"`；CNH 英文名稱寫 `CNH`。比對時忽略空白、括號全半形。只用 §2.3 現有佔位符，不需要新機制。
- **發行機構全名**（`issuer_name.citi`）：`Citigroup Global Markets Holdings Inc.`（只有英文法人名）；封面、第一章 §3、第二章 §1(1) 三處相同；封面名稱後的 `(“CGMHI”)` 不算名稱。
- **保證機構名稱**（`guarantor_name.citi`，cover／ch1／ch2）：三處皆 `Citigroup Inc.花旗集團`；地址不核對。投資人須知警語 5. 用 cover；相關機構 2. 只寫中文 `花旗集團`，視為等價寫法（`guarantor_name_equivalents.citi = ["花旗集團"]`，2026-10-14 確認）。
- **總代理人名稱**（`agent_name.citi`，cover／ch2）：`花旗環球證券股份有限公司`；地址電話、負責人不核對。
- **商品種類**（`product_type.citi`）：`連結股權結構型商品`，不隨標的數改變。
- **發行不成立門檻**（`minimum_issue_size.citi`）：第四章 §7 `等值30 萬美元`（JPY、CNH 商品也寫美元；2026-10-14 確認納入）。
- **最低金額倍數**：最低申購、最低加購、最低贖回都 = 單位面額（不需要 `min_amount_multiples.citi`）。
- **受託機構電話**：封面寫 `(+886)2-5556-1313` → `phone_equivalents` 加入。
- **配息率**：= 年利率 ÷ 12 四捨五入 4 位，文件可能去尾零（`0.375%`），比數值。
- 發行機構／保證機構／總代理人地址電話、情境分析算式不核對。

### 2.10 JPM 的差異（2026-10-15 確認，Issue #233；已實作）

細節見 [JPM 核對規則](jpm-check-rules.md) §3.2、§3.6。審查通過日期、負責人、受託機構名稱與電話（`+886 2 5556 1313` 已在 `phone_equivalents`）、費率、發行價格、面額預設值、刊印日期規則與 BARC 相同。不同的有：

- **固定警語**（`risk.fixed_warning_by_issuer.jpm`，2026-10-15 確認視為正確）：`【RR4】`、`【證券交易所交易證券】` 無【】；4 處 `受託機構` 寫 `受託或銷售機構`；OSU 段改寫成 `…投資本商品之專業投資人(包括OSU客戶)以下稱「投資人」)。`（括號不成對，照原文）；結尾多一句 `本商品風險等級(Product Risk Rating)由受託機構釐定及提供，發行機構及總代理人不對此評級之準確性負任何責任。`。說明書出現 3 次（封面 1.、第 I 部分第 2 項、第 III 部分 6.(1)）；投資人須知 1 次（`iis.fixed_warning_occurrences.jpm = 1`）。
- **風險等級寫法**（`risk.level_formats.jpm`）：沒有【】，寫在 `風險程度等級為`、`歸類為` 之後（同 MS 的無括號寫法）。
- **禁用語允許片語**（`wording.allowed_phrases`，所有上手生效）：加入「受託投資/受託買賣」（警語 7.，說明書 2 次、投資人須知 1 次）。本機全部樣本只有 JPM 有這個寫法，其他上手結果不變。
- **商品名稱樣板**（`product_name.jpm`）：中文 `摩根大通{天期}個月{幣別中文}計價連結股權固定配息可自動提前出場結構型商品(無擔保)(不保本)(下稱「本商品」)`；英文 `JPMorgan {天期}Months {幣別代碼} Link to Equity Securities Callable Fixed Coupon Note (Unsecured)(Non-Principal Protected)`。**不分記憶式、KI、標的數**，不用 `{autocall_*}`、`{underlying_*}` 佔位符；英文忽略空白比對（`ignore_whitespace_en`）。
- **發行機構全名**（`issuer_name.jpm`）：`荷蘭商J.P. Morgan Structured Products B.V.`；封面（`發行機構註冊地：` 句內）、第 II 部分 1.(1) 兩處相同；第 I 部分沒有出處。JPM 目前只有這一個發行主體（2026-10-15 確認）。
- **保證機構名稱**（`guarantor_name.jpm`）：cover、ch2 = `美商摩根大通銀行股份有限公司(JPMorgan Chase Bank, N.A.)`；ch1（第 I 部分第 3 項簡稱）= `美商摩根大通銀行股份有限公司`；地址不核對。投資人須知相關機構 2. 用 cover、警語 5. 用 ch1。
- **總代理人名稱**（`agent_name.jpm`，cover／ch2）：`美商摩根大通銀行股份有限公司台北分公司`；地址電話、負責人不核對。
- **商品種類**（`product_type.jpm`）：`股權連結結構型商品`，不隨標的數改變（同 BNP）。
- **發行不成立門檻**：範本沒有金額門檻（只寫保證機構信用評等條件），不設 `minimum_issue_size.jpm`。
- **最低金額倍數**：最低申購、最低加購、最低贖回都 = 單位面額（不需要 `min_amount_multiples.jpm`）。
- **受託機構地址**：封面、第 II 部分 3.(3)、投資人須知一、4. 結尾的 `(營業活動所在地)` 不屬於地址（同 HSBC、MS），去掉後 = `distributor.address`。
- **受理申購日期**：只有開始受理申購日期 1 處（≤ 交易日）。
- **年利率**：說明書只寫 `每單位面額 x Y% x 1/12`，沒有月配息率，不做月配息率推算。
- 發行機構／保證機構／總代理人地址電話、情境分析算式不核對。

### 2.11 Nomura 的差異（2026-10-15 確認並實作，Issue #239）

細節見 [Nomura 核對規則](nomura-check-rules.md) §3.2、§3.6。負責人、受託機構名稱、電話（`+886 2 5556 1313` 已在 `phone_equivalents`）與地址（`台北市民生東路三段158號6樓` 已在 `address_equivalents`）、費率、發行價格、面額預設值、刊印日期規則與 BARC 相同。不同的有：

- **審查通過日期**（`distributor.approval_dates`，所有上手生效）：加入 **2025-06-24**（2026-10-15 作業人員確認是受託機構真實的歷次審查日期；Nomura 2025 年的兩份樣本寫這一天）。比現有最早一筆早，PANEL 只能加較晚的日期，所以直接改設定檔。交易日早於 2025-12-18 的既有上手樣本從「轉人工覆核（`approval_date_not_configured`）」改成和 2025-06-24 比：本機其他上手樣本只有 2 份結果改變（SG、MS 各 1 份 2025 年下半年交易、寫 2025-06-24 的說明書，由轉人工覆核改為通過），其餘不變。
- **固定警語**（`risk.fixed_warning_by_issuer.nomura`，2026-10-15 確認視為正確）：= SG 版本（無【】、有「*」、結尾有句號）。說明書只出現 **2 次**（封面(一)、第三章 6.(1)；第一章第 2 項只寫等級）——新增上手別的說明書次數設定 `risk.fixed_warning_occurrences_by_issuer.nomura = 2`（沒列出的上手沿用 `fixed_warning_occurrences = 3`）；投資人須知 1 次（`iis.fixed_warning_occurrences.nomura = 1`）。
- **風險等級寫法**（`risk.level_formats.nomura`）：沒有【】，寫在 `風險程度等級為`、`歸類為` 之後；另有 `商品風險程度：RR4`（說明書第一章第 2 項）、`本商品風險程度：RR4`（投資人須知第二 3.）。
- **商品名稱樣板**（`product_name.nomura`）：中文 `Nomura International Funding Pte. Ltd.發行{天期}個月{幣別中文}計價固定配息({記憶式}自動提前出場){標的}結構型商品(無擔保)`（`memory_zh = "記憶式"`；`single_zh = "股票連結"`、`basket_zh = "連結一籃子股票"`）；英文 `{天期}-Month {幣別代碼} Autocall Fixed Coupon Notes {記憶式}linked to {標的} issued by Nomura International Funding Pte. Ltd. (unsecured)`（`memory_en = "with Memory Knock-Out "`；`single_en = "a Single Stock"`、`basket_en = "a basket of stocks"`）。不分 KI、Non-Call；英文忽略空白比對（`ignore_whitespace_en`）。封面名稱後的 `(ISIN No. … / TDCC…)` 不屬於名稱。
- **發行機構全名**（`issuer_name.nomura`）：`Nomura International Funding Pte. Ltd.`（只有英文名）；封面、第二章 1.(1) 兩處相同；第一章沒有出處。Nomura 目前只有這一個發行主體（2026-10-15 確認）。
- **保證機構名稱**（`guarantor_name.nomura`）：cover = ch1 = ch2 = `Nomura Holdings, Inc.`；地址電話不核對。投資人須知第一 2. 用 cover。
- **總代理人名稱**（`agent_name.nomura`，cover／ch2）：`香港商野村國際證券有限公司台北分公司`；地址電話、負責人不核對。
- **商品種類**（`product_type.nomura`）：`連結股權之結構型債券`，不隨標的數改變。
- **發行不成立門檻**（`minimum_issue_size.nomura`）：`三十萬美元`（說明書第四章 7.、投資人須知第五 1.；AUD、JPY 商品也寫美元）。
- **最低金額倍數**：最低申購、最低加價申購、最低贖回、增加贖回單位（第四章、第一章第 16 項(1)）都 = 單位面額（不需要 `min_amount_multiples.nomura`）。
- **受託機構電話**：封面與第四章第 15 項 `受託機構聯絡方式：`（`distributor_phone_ch4`，同 BNP）；投資人須知第九 1.。
- **受理申購日期**：開始受理申購日期、接受申購截止日 2 處（≤ 交易日）。
- **配息率**：說明書只寫每期（每月）配息率，沒有年利率；每期配息率 × 12 四捨五入到 2 位 = 年利率，第 14 項情境一、二年化報酬率 = 年利率（2026-10-15 確認）。
- 發行機構／保證機構／總代理人地址電話、情境三算式不核對。

### 2.13 刊印日期與受理申購日（2026-10-15 確認，Issue #254）

OOS 中 HSBC 週五交易、下週一刊印，以及受理申購從交易日前幾天開始的說明書被判錯，作業人員決定放寬（所有上手）：

- **刊印日期**（`doc.print_date`）：交易日（含）≤ 刊印日期 < 發行日（不含）。發行日取同一份文件（說明書或投資人須知）的發行日，讀不到時轉人工覆核。取代原本的「交易日當天到 +1 個日曆日」（設定 `dates.print_date_max_days_after_trade` 已移除）。
- **受理申購日**（`doc.subscription_start_date`）：每個出處（開始、結束）不晚於交易日即可，取代原本的「= 交易日」。

## 3. 對 14 份 BARC 樣本的驗證結果（2026-10-01，本機探勘）

| 檢查 | 結果 |
|---|---|
| 固定風險警語（3 次） | 14 份全部符合 |
| 風險等級 RR4 | 14 份全部符合 |
| 受託機構負責人「林晋輝」（古字晋） | 14 份全部符合 |
| 「受託投資」只出現在允許片語內 | 14 份全部符合（每份 8 次，皆為「受託投資或受託買賣」） |
| 英文商品名稱 | 14 份全部符合 |
| 刊印日期 | 14 份全部符合（交易日當天 2 份、隔天 12 份） |
| 審查通過日期 | 新系列 8 份為 2026-06-11、舊系列 6 份為 2025-12-18，皆為交易日當時最近一次審查的日期；依交易日核對（§2.5）14 份全部符合 |
| 中文商品名稱含「（不保本）」 | 最新格式的 7 份符合；較早的 7 份尚未加入「（不保本）」，屬預期內 |
| 發行機構全名、受託機構名稱／電話／地址、三項費用區間、發行價格 100%（2026-10-02 補驗） | 14 份全部符合 |

### 3.1 對 8 份 HSBC 樣本的驗證結果（2026-10-01，本機探勘）

| 檢查 | 結果 |
|---|---|
| 固定風險警語（HSBC 版本，3 次） | 8 份全部符合；BARC 與 SG、BNP、Nomura 樣本中 HSBC 版本皆出現 0 次 |
| 風險等級 RR4、負責人「林晋輝」 | 8 份全部符合 |
| 「受託投資」只出現在允許片語內 | 8 份全部符合（每份 2 次） |
| 中英文商品名稱 | 8 份全部符合 |
| 刊印日期 | 8 份全部符合（參考性審閱版 = 交易日；最終版 = 交易日隔天） |
| 審查通過日期 | 5 份為 2026-06-11、較早的 3 份為 2025-12-18；依交易日核對（§2.5）8 份全部符合 |

審查通過日期保存歷次日期（§2.5），舊文件重新核對仍正確；其他基準只保存「目前」的版本，舊文件的中文名稱沒有「（不保本）」仍會判不一致。

## 4. 不在審查標準內（使用者確認不核對）

- 銷售說明書（Offering Circular）版本日期
- 發行機構財報年度、發行總值、信用評等
- 發行機構、總代理人、計算代理機構等巴克萊方的負責人
- 情境分析中取決於假設持有期間與價格的試算（有利情況年化率、最差情況報酬率）；其餘情境項目見 [BARC 核對規則](barc-check-rules.md) §3.7
- 投資人須知（IIS）另見[投資人須知核對規則](iis-check-rules.md) §3 C 類（2026-10-08）：沿用本檔基準，固定警語次數另訂
