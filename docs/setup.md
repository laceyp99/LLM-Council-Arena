# Setup

## Requirements

- **Python 3.13 or newer.** The package raises an error on import with an older
  interpreter. A `.python-version` file pins 3.13 for version managers such as `uv` or
  `pyenv`.
- **An OpenRouter API key** for live generation. Create one at
  <https://openrouter.ai/keys>. Without a key the app still opens, but nothing can be
  generated (see [Degraded mode](#degraded-mode)).

## Install

Clone the repository and install it from the repository root. The examples use an
editable install (`-e`) so code changes apply without reinstalling. A regular install
works too.

With [uv](https://docs.astral.sh/uv/):

```powershell
uv venv -p 3.13 .venv
uv pip install -p .venv -e ".[dev]"
```

With the standard library `venv` and `pip`:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

On macOS or Linux, replace `.venv\Scripts\python.exe` with `.venv/bin/python` and
`py -3.13` with `python3.13`.

Optional dependency groups (extras):

| Extra | Adds | Install with |
| --- | --- | --- |
| none | Just the app | `-e .` |
| `dev` | `pytest`, `ruff`, `pre-commit` | `-e ".[dev]"` |
| `docs` | `mkdocs-material` | `-e ".[docs]"` |

Combine them as `-e ".[dev,docs]"`.

## Add your API key

Copy the example file and put your key in it:

```powershell
Copy-Item .env.example .env
```

```env
OPENROUTER_API_KEY="your_openrouter_api_key_here"
```

The app reads `.env` from the folder you start it in, so keep it in the repository root
and start the app from there. A regular environment variable with the same name works
too. `.env` is listed in `.gitignore`, and CI fails if one is ever committed.

## Run

```powershell
.venv\Scripts\python.exe -m arena
```

The editable install also adds a console script, `.venv\Scripts\llm-council-arena.exe`,
which does the same thing.

Gradio prints the local address when it starts, usually <http://127.0.0.1:7860>. If that
port is busy, Gradio picks the next free one.

Start it from the folder that holds your `.env`. That same folder is where the app saves
votes and logs (see [Data files](data.md)). Nothing is saved until you submit a vote.

On startup the app:

1. Creates `arena_logs/` and an empty leaderboard file if they do not exist.
2. Checks the API key against OpenRouter's `/key` endpoint.
3. Downloads the OpenRouter model catalog and keeps the text-capable models.
4. Opens the UI with the three default models preselected.

The catalog is loaded once. Restart the app to pick up new OpenRouter models or a changed
key.

## Degraded mode

Degraded mode happens when the key is **missing**, the key is **rejected** by OpenRouter,
or the **model list fails to download**. The app still opens so you can see the layout,
but it locks generation:

- A red banner at the top of the Arena tab says OpenRouter is not ready.
- The provider and model dropdowns and the **Send** button are disabled.
- The panels show a built-in fallback list of the three default models.
- If a prompt is submitted anyway (for example through Gradio's API), every panel reports
  why generation is blocked, and no request is sent.

This happens when:

| Cause | What to check |
| --- | --- |
| No key found | `.env` exists in the folder you started the app from, or the variable is set in the shell that starts the app |
| Key rejected by OpenRouter | The key is active at <https://openrouter.ai/keys> |
| Catalog download failed | Network access to `openrouter.ai` |

Fix the cause, then restart the app.

## Troubleshooting

**"LLM Council Arena requires Python 3.13 or newer."** The virtual environment was created
with an older Python. Delete `.venv` and recreate it with 3.13.

**A panel shows `HTTP 402` or "Insufficient credits".** The OpenRouter account is out of
credit. The other panels still finish, and the round can still be ranked.

**A panel shows `HTTP 404` or a model-not-found error.** That model has been removed from
OpenRouter since the catalog loaded. Pick another model, or restart to refresh the catalog.

**"Vote could not be saved"** or **"Leaderboard unavailable".** A data file is not valid
JSON, or (on Windows) another process held its lock for more than 5 seconds. See
[Data files](data.md#resetting-or-recovering).
