# Code Iteration Skill

## Purpose

Keep daily bot improvements small, evidence-driven, and reviewable. This skill is used by the maintenance agent to run a fixed auto-iteration workflow: profile, draft PR, agent review, apply a narrow approved class of patch, test, profile again, and record rollback notes.

## Guardrails

- Automatic source edits are allowed only inside the workflow below and only for narrow, low-risk changes.
- Strategy files may be created or changed automatically only as bounded candidates; activation requires the fixed gates in `policy.yaml`.
- Prefer one narrow improvement per proposal.
- Every proposal must include profile evidence, test status, risk, rollback notes, and expected user impact.
- Prioritize fixes for correctness, safety, rate limits, observability, and clear performance hot paths.
- Do not add paid APIs, real broker trading, or credential handling changes without explicit user approval.
- Never print secrets. Redact tokens in logs and reports.
- If profile data is noisy or missing, propose instrumentation first.
- If the repo is not a git repository, write a local PR draft and review record instead of trying to create a remote PR.
- Never modify real broker trading boundaries, credentials, launchd labels, or notification recipients automatically.
- Do not create a new paid data dependency automatically.
- New strategy ideas default to `status: candidate`; they may be promoted to `active` only after py_compile, pytest, strategy dry-run, and backtest gates pass.

## Fixed Workflow

1. Run profile suite and health checks.
2. Identify one narrow issue.
3. Write local PR draft.
4. Run agent review checklist.
5. Apply patch only if it is low-risk and bounded.
6. Run `py_compile`, unit tests, and a profile smoke check.
7. Promote eligible strategy candidates to active only when the activation gates pass.
8. Record changed files, test output, profile deltas, strategy activation decisions, and rollback notes.

## Daily Output

- Local PR draft under `.portfolio_bot/pr_drafts/`
- Review report under `.portfolio_bot/code_iterations/`
- Memory record with kind `daily_review` and strategy `code_iteration_agent`
- Change record with touched files and rollback notes

## Agent Review Checklist

- Is the proposed change bounded to one module or behavior?
- Does it reduce recurring runtime cost or failure rate?
- Does it preserve dry-run semantics?
- Does it preserve the no-real-trading boundary?
- Are tests and profile data included?
- Is rollback straightforward?
