# Development

Commands assume the editable install from [Setup](setup.md#install) with the `dev` extra.
On macOS or Linux, use `.venv/bin/python` in place of `.venv\Scripts\python.exe`.

## Checks

These are the same three checks CI runs:

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest
```

Ruff formats with **tabs** and double quotes, and lints for pycodestyle errors, pyflakes,
and import order (`E`, `F`, `I`). Line length is not enforced.

### Tests

The suite runs offline in about 20 seconds. OpenRouter calls are replaced with fakes, and
tests that write files point the data paths at a temporary folder.

| Folder | Covers |
| --- | --- |
| `tests/unit/core/` | Catalog normalization, reasoning detection, stream parsing and error chunks |
| `tests/unit/state/` | Round state and ranking logic |
| `tests/unit/ui/` | Chat message and leaderboard formatting |
| `tests/unit/app/` | Request payloads and persistence helpers |
| `tests/integration/` | Full streaming rounds, vote submission and rollback, callbacks, building the Gradio UI, and import side effects |

`tests/conftest.py` provides a `use_catalog` fixture that swaps in a test model catalog.

### Pre-commit

```powershell
.venv\Scripts\pre-commit.exe install
```

The hooks run Ruff (with fixes), whitespace and end-of-file fixers, YAML and TOML checks,
a large-file check, line endings normalized to CRLF, and the full test suite.

> **The pytest hook uses your PATH.** It runs whichever `pytest` is first on `PATH`, not
> necessarily the one in `.venv`. If a global Python with a different Gradio comes first,
> tests fail for reasons unrelated to your change. Activate `.venv` (or put
> `.venv\Scripts` first on `PATH`) before committing.

### Docs

These pages are plain Markdown and render on GitHub directly, diagrams included. To build
them as a site with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/):

```powershell
uv pip install -p .venv -e ".[docs]"
.venv\Scripts\python.exe -m mkdocs serve
```

`mkdocs build --strict` writes the static site to `site/`, which is git-ignored.

Material for MkDocs reaches end of life on May 5, 2027. Its maintainers point to
[Zensical](https://zensical.org) as the successor, and it reads an existing `mkdocs.yml`.

## CI and repository automation

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | Every push and pull request | Fails if a database, `.env`, `votes.jsonl`, or `arena_logs/` file is tracked, then runs the three checks above on Python 3.13 |
| `issue-implement.yml` | The owner adds the `agent-ready` label to an issue, or a manual run | Runs the Codex agent with `.github/codex/prompts/issue-implement.md` to implement the issue, runs the checks, and opens a draft pull request |
| `pr-review.yml` | A same-repository pull request is marked ready for review, or a manual run | Runs the Codex agent with `.github/codex/prompts/pr-review.md` and posts advisory review notes |

The two agent workflows need an `OPENAI_API_KEY` repository secret and optionally the
`CODEX_MODEL` and `CODEX_EFFORT` variables. The issue workflow refuses to run for anyone
except the repository owner. An archived repository accepts no new issues, labels, or
pull requests, so nothing triggers them here. They work as written in a fork that sets the
secret.

## Reviving the project

If you pick this up after the archive date, check these first, roughly in order of how
likely they are to have changed:

1. **Default models.** OpenRouter retires model ids. The app still starts if a default
   is gone (it picks another model from the same provider), but update
   `DEFAULT_MODEL_IDS` in `arena/ui/config.py` and the matching fallback list in
   `arena/core/models.py`.
2. **Reasoning effort levels.** The list in `arena/core/reasoning.py`
   (`none` through `xhigh`) mirrors OpenRouter's at archive time.
3. **Gradio.** Pinned to `>=6.10,<7.0`. The UI code uses `gr.ChatMessage` metadata,
   `gr.on`, and `Chatbot(buttons=...)`, which are the parts most likely to break on a
   major upgrade.
4. **Python.** Tested on 3.13. CI and `.python-version` pin it.
5. **OpenRouter API.** The client uses `/key`, `/models`, and streaming
   `/chat/completions`, and reads `reasoning_details` with a fallback to the older
   `reasoning` and `reasoning_content` delta fields.

6. **Docs tooling.** See the Material for MkDocs end-of-life note under [Docs](#docs).

Run the three checks after any upgrade. They do not need an API key.

## Ideas that were not built

Left here for anyone who forks the project:

- Rebuilding `meta.json` from `votes.jsonl` and the session folders.
- An Elo or Bradley-Terry rating instead of raw win counts.
- Multi-turn rounds.
- Showing the extra totals already in `meta.json` (2nd and 3rd places, errors, tokens,
  cost) on the Leaderboard tab.
