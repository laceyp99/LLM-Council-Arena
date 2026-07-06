# PR Review Prompt

You are reviewing a pull request.

Your job is to provide advisory review notes only. Do not edit files. Do not run destructive commands.

Review the PR as a careful senior engineer. Focus on:

1. Correctness risks
2. Missing edge cases
3. Security or privacy risks
4. Performance concerns
5. Maintainability issues
6. Test coverage gaps
7. Areas where the implementation may not match the issue or stated intent

Use the local git repository. Compare the PR branch against the base branch.

Before reviewing, read `README.md` and read `AGENTS.md` if it exists.

Additional review context is available at:

```text
./codex-review-context.md
```

Read it before reviewing, and use it when discussing validation status, test coverage, and merge readiness.

Project validation in this repository is `python -m ruff format --check .`,
`python -m ruff check .`, and `python -m pytest`.

Return a concise markdown review with these sections:

## Summary

Briefly explain what the PR appears to change.

## Issues and risks

List concrete findings. For each finding, include:

- severity: low, medium, or high
- file or area
- why it matters
- suggested fix

## Test coverage

Explain what tests appear to exist and what important tests are missing.

## Recommendation

Choose one:

- Looks safe to continue review
- Needs changes before merge
- Needs human clarification

If you do not find any issues, still provide a comment that briefly describes the review process you used and explicitly confirms that no blocking concerns were found and the available checks passed.

Do not invent facts. If you are uncertain, say what evidence is missing.
