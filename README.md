# LLM Council Arena

LLM Council Arena is a Gradio app for running the same prompt against three LLMs, showing their responses anonymously, and recording ranked votes after each round.

It is built around OpenRouter for live model generation, but it can still start without an API key so you can inspect the UI and model-selection flow in a degraded mode.

## What it does

- Runs one prompt against three selected models in parallel.
- Randomizes display order so the voter sees anonymous panels instead of fixed model identities.
- Streams model output into side-by-side chat panels.
- Shows per-model reasoning controls when OpenRouter metadata reports support.
- Captures provider-exposed reasoning traces when requested and available.
- Records vote rankings and round metadata to local JSON files.
- Builds a local leaderboard from accumulated vote history.

## How a round works

1. Choose one model for each of the three panels.
2. Adjust any reasoning controls shown for those models.
3. Enter a prompt and submit it.
4. The app streams all three responses concurrently.
5. Responses are displayed anonymously as `Response A`, `Response B`, and `Response C` until after voting.
6. Rank the three outputs.
7. Submit the vote to persist the round result and refresh the leaderboard.

## Reasoning controls

Reasoning controls are shown independently for each selected model and are driven by
OpenRouter model metadata. If the selected model does not report supported reasoning
parameters, the app hides the reasoning controls for that panel.

Depending on the selected model, the selector area may show:

- An effort dropdown for models that support OpenRouter reasoning effort values.
- No reasoning control for models that do not expose effort levels.

When reasoning is actively requested, the app asks OpenRouter to expose reasoning traces by
sending `exclude: false`. Unsupported reasoning fields are omitted from the request payload
instead of forcing unsupported parameters.

Where OpenRouter exposes pricing metadata, the selector shows a lightweight per-1M-token
cost hint for input and output prices. These hints are approximate and are not a full cost
estimate because actual reasoning token use and provider routing can vary.

## Installation

1. Create and activate a virtual environment.
2. Install the dependencies:

```powershell
py -3.13 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .
```

3. Copy the example environment file and fill in your OpenRouter key:

```powershell
Copy-Item .env.example .env
```

4. Update `.env` with your real key:

```env
OPENROUTER_API_KEY="your_openrouter_api_key_here"
```

## Running the app

Start the Gradio app from the workspace root:

```powershell
python -m arena
```

If you install the project into your environment, you can also use the console script:

```powershell
llm-council-arena
```

The app launches locally at the default Gradio address. The app configuration currently identifies itself as `LLM Council Arena` and uses `http://localhost:7860` as the site URL when calling OpenRouter.


## Project layout

```text
arena/
  __main__.py              # Package entrypoint for `python -m arena`
  app.py                   # Gradio app assembly and launch target
  core/                    # OpenRouter API client and model catalog loading
  state/                   # Round state and voting helpers
  ui/                      # Display helpers and app configuration
tests/                     # Unit tests for package behavior
arena_logs/                # Generated runtime logs
votes.json                 # Generated vote history
```

## Degraded mode without an API key

If `OPENROUTER_API_KEY` is missing, the app still starts and loads a fallback model catalog for the selectors. Live generation is blocked in that mode, and each response panel will report that the API key is missing.

This is intentional behavior in the current codebase.

## Data written by the app

The app writes local JSON artifacts during use:

- `votes.json`: append-only vote records
- `arena_logs/meta.json`: aggregate leaderboard and round summary data
- `arena_logs/sessions/<timestamp>_<round_id>/round.json`: round metadata
- `arena_logs/sessions/<timestamp>_<round_id>/generation.json`: generation output, errors, applied reasoning payloads, token/cost stats, and reasoning details
- `arena_logs/sessions/<timestamp>_<round_id>/histories.json`: serialized chat histories per panel
- `arena_logs/sessions/<timestamp>_<round_id>/vote.json`: submitted ranking order

These files are runtime data, not source code, so the included `.gitignore` excludes them.
