<div align="center">
  <img src="docs/assets/logo.png" alt="LLM Council Arena Logo" width="50%">
</div>

LLM Council Arena is a local Gradio app for blind side-by-side model comparison. You send
one prompt to three [OpenRouter](https://openrouter.ai) models at once, read their answers
as `Response A`, `Response B`, and `Response C`, rank them, and the app keeps a running
leaderboard of which models win.

> **Status:** archived. The project is kept as a finished reference and is not maintained.
> Tests pass on Python 3.13 with Gradio 6.x as of the archive date.

## Features

- Three concurrent streaming responses, shuffled into anonymous panels every round.
- Model names revealed after you submit your ranking.
- Per-model reasoning effort controls and price hints, driven by OpenRouter metadata.
- Reasoning traces when the provider exposes them, plus a stats footer with timing,
  tokens, and cost.
- Every submitted round saved to local JSON files, with a win-count leaderboard.

## Quick start

Requires Python 3.13+ and an [OpenRouter API key](https://openrouter.ai/keys).

```powershell
git clone https://github.com/laceyp99/LLM-Council-Arena.git
cd LLM-Council-Arena
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env   # then put your key in .env
.venv\Scripts\python.exe -m arena
```

Then open the address Gradio prints, usually <http://127.0.0.1:7860>.

The app reads `.env` from, and saves its data to, the folder you start it in.
**Nothing is saved unless you submit a vote.**

If the key is missing, the key is rejected, or the OpenRouter model list fails to
download, the app still opens in a degraded mode. A red banner explains the problem, and
the model dropdowns and the **Send** button are disabled until you fix it and restart.

## Documentation

Full docs live in [`docs/`](docs/index.md):

- [Setup](docs/setup.md): install options, the API key, degraded mode, troubleshooting
- [Using the arena](docs/using-the-arena.md): rounds, voting, reasoning controls, leaderboard
- [Data files](docs/data.md): what is written to disk, file formats, resetting
- [Architecture](docs/architecture.md): module map and how a round flows through the code
- [Development](docs/development.md): tests, CI, and what to check before reviving the project

## Development

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest
```

## License

[MIT](LICENSE)
