# 0002: 本機 Python CLI，PDF 擷取採用 PyMuPDF

- Status: Accepted
- Date: 2026-10-01

## Context

第一個實作 Issue（#7）需要可重現的本機執行環境、保留頁碼與座標的 PDF 文字擷取，以及讀取上手詢價 Excel。樣本皆為 Word 匯出的文字型 PDF（見 BARC 範本規格）。PDF 套件授權會影響日後散布方式。決策討論見 Issue #7 留言（2026-10-01）。

## Decision

- 執行環境：本機 Python CLI（`fcn-check`），Python ≥ 3.11（使用標準庫 `tomllib` 讀設定）。
- PDF 擷取：PyMuPDF（`get_text("dict")` 逐行文字與 bbox）。PyMuPDF 為 **AGPL-3.0**；本工具僅限公司內部本機使用，不對外散布、不提供網路服務。
- Excel：openpyxl（`data_only=True` 讀取儲存格值）。
- 依賴版本鎖定在 `constraints.txt`，CI 以相同版本安裝。

## Alternatives considered

- pdfplumber（MIT）：表格工具較多，但 BARC 版面用座標＋錨點已足夠；探勘階段已以 PyMuPDF 驗證 14 份樣本，換套件需重新驗證。
- 同時依賴兩個 PDF 套件：增加維護與差異來源，首版不採用。

## Consequences

- 若日後要對外散布本工具或以網路服務提供，必須重新評估 AGPL 義務或改用其他套件（需另立 ADR，並以真實樣本回歸測試）。
- 合成測試 PDF 也以 PyMuPDF 產生（內建 CJK 字型），CI 不需安裝系統字型。
