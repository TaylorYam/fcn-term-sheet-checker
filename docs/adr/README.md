# Architecture Decision Records

Use an Architecture Decision Record (ADR) for a choice that has a lasting effect on system structure, dependencies, data, operations, security, or compatibility. Keep routine implementation details in the code and pull request.

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
