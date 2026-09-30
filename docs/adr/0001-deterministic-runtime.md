# 0001: 第一版採規則式核對，不使用 LLM runtime

- Status: Accepted
- Date: 2026-09-30

## Context

使用者已指定正式核對流程不依賴 LLM，先針對固定 issuer／範本建立可稽核流程。本次只建置架構文件；不代表解析品質或業務規則已驗證。來源：[初始化 Issue #1](https://github.com/TaylorYam/fcn-term-sheet-checker/issues/1)。

## Decision

採 PDF text extraction → template/parser → normalized schema → rule engine → exception report。文字型 PDF 優先評估 PyMuPDF/pdfplumber，掃描頁於後續階段加入本地 OCR。下單資料獨立標準化，作為預期值來源。缺值、歧義、未知範本或不支援語意必須進例外報告與人工覆核。

第一版 production runtime 不包含模型 SDK、金鑰、模型呼叫或可啟用的 LLM fallback。保留 extraction adapter 邊界供未來重新評估，但不建立未使用的抽象框架。

## Alternatives considered

- LLM 直接擷取與判定：不符合本次需求，且仍需證據驗證與可稽核規則。
- 全文件純 regex：容易破壞表格、跨頁與多標的關係；改用版面錨點與範本 parser，regex 只處理局部值。
- 全文件 OCR：對已有可用文字層的頁面增加誤差與成本，僅在必要頁面使用。

## Consequences

可將欄位、規則與來源證據版本化，也需維護範本及回歸案例。確定性不保證正確性，必須以人工標註樣本與拒絕不確定輸入驗證。OCR 引擎、下單來源與細部業務規則仍待確認；後續變更 LLM 政策需另立 ADR。
