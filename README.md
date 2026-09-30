# AI-Assisted Project Template

A GitHub-first starting point for projects built by people and AI coding agents. It provides a lightweight contribution workflow, issue and pull request forms, a starter CI check, and guidance for keeping credentials and local runtime data out of Git.

## Start a project from this template

1. On GitHub, open this repository and select **Use this template → Create a new repository**. Choose the new repository's owner, name, visibility, and other settings.
2. Clone the new repository and open it in your editor or coding agent.
3. Create a **Project bootstrap** issue describing the purpose, users, chosen stack, and first deliverable.
4. Ask the coding agent to read `AGENTS.md`, this README, `.github/`, and `docs/`; inspect the repository; then propose project-specific changes before editing.
5. Tailor `.gitignore`, `.env.example`, `docs/architecture.md`, and `.github/workflows/ci.yml` to the project. Add tests and stack-specific quality checks before feature work.
6. Review and merge the bootstrap pull request, then begin feature work through issues and pull requests.

If GitHub does not show **Use this template**, an owner can enable it in **Settings → General → Template repository**.

## Contribution flow

For planned changes, use:

`Issue → branch or worktree → plan → implementation → validation → commit and push → pull request → review and CI → merge`

Keep `main` as the stable integration branch. Make routine changes on a task branch and merge them through a reviewed pull request. Small changes still follow the same branch and review path; the amount of planning and testing should match their scope.

Use the issue templates for tasks, bugs, and architecture decision proposals. Link the issue from the pull request so the intent and implementation stay connected.

Agents should follow the Work Routing in `AGENTS.md` before creating an Issue. Agent conversations default to plain Traditional Chinese (Taiwan usage); agent-created GitHub Issues default to Traditional Chinese.

## Repository guide

- `AGENTS.md` — shared AI development policy for Claude Code, Codex, and human contributors.
- `CLAUDE.md` — Claude Code entry point that imports `AGENTS.md`.
- `.github/ISSUE_TEMPLATE/` — task, bug, and architecture decision forms.
- `.github/pull_request_template.md` — review checklist and validation record.
- `.github/workflows/ci.yml` — starter whitespace check; add the project's formatter, tests, type checks, and build here.
- `.gitignore` — common generated files, local configuration, credentials, and runtime data exclusions.
- `.env.example` — names and safe placeholders for environment variables; never put real secrets here.
- `docs/architecture.md` — current system overview and pointers to important design choices.
- `docs/adr/` — durable records of significant architecture decisions.

## Template maintenance

Keep this repository stack-neutral. When changing the workflow, update the relevant source file and this guide if the change affects how a new project is bootstrapped. Use Git tags such as `v1.0.0` to identify template versions used by new repositories.

