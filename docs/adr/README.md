# Architecture Decision Records

Use an Architecture Decision Record (ADR) for a choice that has a lasting effect on system structure, dependencies, data, operations, security, or compatibility. Keep routine implementation details in the code and pull request.

## 現行決策索引

| ADR | 狀態與內容 |
|---|---|
| [0001](0001-deterministic-runtime.md) | Accepted：規則式核對，不在正式流程使用 LLM |
| [0002](0002-python-cli-pymupdf.md) | Accepted：Python 與 PyMuPDF；目前另有本機 PANEL |
| [0003](0003-public-github-panel-update.md) | Superseded by 0006：舊版公開 GitHub 自動更新 |
| [0004](0004-reference-sheet-as-check-source.md) | Superseded by 0008：早期參考條件表輸入／輸出流程 |
| [0005](0005-issuer-adapter-and-shared-rules.md) | Superseded by 0009：早期上手 adapter 與共用規則邊界 |
| [0006](0006-panel-maintainer-only-no-self-update.md) | Accepted：維護者本機 PANEL，移除自動更新 |
| [0007](0007-iis-paired-with-term-sheet.md) | Accepted：TS／IIS 成對核對，三方一致才回填 |
| [0008](0008-reference-sheet-and-import-format.md) | Accepted：參考條件表唯一來源、固定匯入格式輸出 |
| [0009](0009-shared-rules-defined-per-rule.md) | Accepted：共用規則只實作一次，語意逐條定義 |

ADR 保留當時決策，不改寫歷史樣本數或當時程式切點；目前模組與支援範圍另見[架構](../architecture.md)、[支援範圍](../support-matrix.md)。

## Create a record

1. Discuss the question and options in an issue using the architecture decision proposal template.
2. After the decision is made, add a numbered file such as `0001-use-managed-database.md`.
3. Record the context, decision, alternatives, and consequences. Keep the record concise and specific.
4. Link to the ADR from `docs/architecture.md` and from relevant code or documentation when useful.

## Suggested format

```markdown
# 0001: Decision title

- Status: Proposed | Accepted | Deprecated | Superseded by [NNNN](NNNN-title.md)
- Date: YYYY-MM-DD

## Context
What problem and constraints require a decision?

## Decision
What did the project decide?

## Alternatives considered
What other options were evaluated, and why were they not selected?

## Consequences
What benefits, costs, risks, and follow-up work result?
```

Treat accepted ADRs as historical records. When a decision changes, add a new ADR and mark the earlier record as superseded instead of rewriting its history.
