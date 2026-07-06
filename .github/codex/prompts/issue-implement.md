# Issue Implementation Prompt

You are implementing a solution for a GitHub issue.

The issue context is available at:

```text
./issue-context.json
```

Your job:

1. Read the issue context.
2. Read `README.md`.
3. Read `AGENTS.md` if it exists.
4. Inspect the repository.
5. Identify the smallest safe implementation that addresses the issue.
6. Make code changes.
7. Add or update tests when practical.
8. Avoid unrelated refactors.
9. Avoid changing public behavior beyond what the issue requires.
10. Do not touch secrets, credentials, deployment settings, or production data.
11. If the issue is ambiguous or unsafe, do not force a solution. Leave notes explaining the blocker.

Before editing, determine:

- What files are likely relevant
- What behavior needs to change
- What tests or checks should validate the fix

Runtime and validation guidance:

- You are running on an Ubuntu GitHub Actions runner in a temporary worktree.
- Use non-interactive, Linux-compatible commands. Do not start servers, watchers, or tools that wait for user input.
- The workflow will run the repository's authoritative validation after you finish. For this repository, that is `python -m ruff format --check .`, `python -m ruff check .`, and `python -m pytest`.
- During your session, prefer focused tests that cover the files or behavior you changed, plus quick lint or format checks when practical.
- If a broad validation command appears to hang or stays silent for a reasonable period, interrupt it once, record the command as inconclusive, and finish the handoff so the workflow validation step can run.
- For broad pytest runs, prefer `python -m pytest` over quiet modes so progress is visible in the Actions log.

After editing, return a markdown summary with:

## Summary

What changed.

## Files changed

List the important files and why they changed.

## Validation

List what checks you ran or what checks should be run.

## Risks

Call out any risks, assumptions, or places needing human review.

## Follow-up

Mention anything the human maintainer should do before merging.
