# AI Development Guide

This root-level file is the repository's shared AI development policy. It defines **when** to use each workflow; skills define **how** to carry it out. Follow an explicit task request when it narrows or extends this guide.

## Work routing

Choose the route before creating an Issue:

- New feature, important requirements unclear: `/grill-with-docs` → `/to-spec` → `/to-tickets` only if one Issue / PR is too large → `/implement` → `/code-review`. Grill only genuine uncertainties.
- New feature, requirements clear: inspect relevant code → create a GitHub Issue → `/implement <issue>` → `/code-review`.
- Bug, cause and correct behavior clear: inspect relevant code → create a GitHub Issue → `/implement <issue>` → `/code-review`. Add a regression test when reasonable.
- Bug, cause unclear: `/diagnosing-bugs` → establish reproduction → identify root cause → confirm correct behavior → create a GitHub Issue → `/implement <issue>` → `/code-review`. Keep hypotheses separate from confirmed causes.
- External or incoming Issue: existing Issue → `/triage <issue>` → `ready-for-agent` → `/implement <issue>` → `/code-review`.

Use `/triage` mainly for Issues created by others. An implementation-ready Issue created by this agent, `/to-spec`, or `/to-tickets` does not need another triage. Use `/to-tickets` only when the work is too large for one Issue / PR.

Decision guide: unknown **what to build** → Grill; known **what to build** → Issue → Implement; unknown **why a bug occurs** → Diagnose; clear bug → Issue → Implement; external Issue → Triage; oversized work → Tickets.

## Before changing files

- Check the current branch and working tree. For normal project work, start from an up-to-date `main` and create a task branch or worktree before editing.
- Read the linked Issue when one is provided. Read `README.md`, `docs/architecture.md`, and relevant ADRs. Inspect related implementation and tests.
- For substantial work, explain the outcome, likely files, approach, risks, and validation before editing. Follow explicit user approval boundaries.
- If an Issue or spec leaves an important choice unresolved that materially affects behavior, data correctness, user experience, architecture, security, payments or real transactions, deletion, API behavior, or backward compatibility, pause at planning and ask the user in plain Traditional Chinese. Make low-risk, reversible implementation choices without interruption.

## GitHub-first workflow

Keep `main` as the stable integration branch. For planned work, follow:

`Issue → branch or worktree → plan → implementation → validation → commit and push → pull request → review and CI → merge`

- Create an implementation-ready Issue before implementation, with a clear goal, scope, and observable acceptance criteria. Agent-created Issue titles and all body sections default to Traditional Chinese (Taiwan usage), including `/to-spec` Issues and `/to-tickets` children. Keep formal technical identifiers in English. Existing English Issues need no rewrite; use English when communicating directly with an English-speaking external contributor.
- Give branches descriptive names, such as `feat/export-report` or `fix/timezone-boundary`.
- Keep changes focused on their Issue and record significant user-visible or architectural tradeoffs in the PR.
- Run relevant checks; add or update tests when behavior changes and report checks that could not run.
- Link the Issue in the PR. Merge after required review and checks pass.
- For a bootstrap repository without an established default branch, initialize the baseline as needed; use branches and PRs for regular work afterward.

## Communication

- Use concise, concrete, plain Traditional Chinese (Taiwan usage) in ordinary conversations with the user, including `/grill-with-docs`. Ask deep questions in everyday language.
- Use technical terms when helpful; explain unfamiliar ones briefly on first use. Common terms such as Git, Branch, PR, API, Database, SQL, and CI need no forced translation. Preserve function names, classes, APIs, libraries, paths, and commands.

## Design and implementation

- Follow existing architecture, naming, formatting, and dependencies unless the task changes them. Make the smallest coherent change that meets acceptance criteria; avoid unrelated refactors and dependencies.
- Update documentation when behavior, setup, or interfaces change. Record lasting architecture decisions in `docs/adr/` using its format.

## Secrets and local data

- Keep credentials, tokens, keys, passwords, and populated `.env` files out of Git, logs, and examples. Put only names and safe placeholders in `.env.example`; use the approved secret store or local environment for real values.
- Keep generated data, caches, build output, and machine-specific configuration out of commits unless the project treats an artifact as source.
- If a secret appears in the working tree or history, stop and report it without copying or repeating it.

## Completion

Review the diff, run applicable validation, and summarize changes, test results, and limitations before handing work back. Leave unrelated user changes untouched.

