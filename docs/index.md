# LLM Council Arena

<img src="assets/logo.png" alt="LLM Council Arena logo" width="50%">

LLM Council Arena is a local Gradio app for blind side-by-side model comparison.
You send one prompt to three models at once, read their answers under anonymous labels
(`Response A`, `Response B`, `Response C`), rank them, and the app keeps a running
leaderboard of which models win.

All generation goes through [OpenRouter](https://openrouter.ai), so any text model in the
OpenRouter catalog can sit in any of the three panels.

## Project status

This repository is **archived**. It is kept as a finished reference, and no further
changes are planned.

At archive time:

- The test suite (116 tests) passes on Python 3.13 with Gradio 6.x.
- CI runs formatting, linting, tests, and a check that no runtime data or secrets are
  committed.
- The default model lineup is `openai/gpt-6.1-terra`, `anthropic/claude-sonnet-5.5`, and
  `google/gemini-4-hydrogen`.

If you want to run or fork it later, start with [Development](development.md#reviving-the-project).
It lists the parts most likely to have gone stale.

## Features

- Streams three model responses concurrently into side-by-side chat panels.
- Shuffles which model lands in which panel every round, so you vote blind.
- Reveals the real model names in the panel headers after the vote is submitted.
- Shows a reasoning effort dropdown for models whose OpenRouter metadata says they support
  it, and a per-1M-token price hint beside it.
- Displays reasoning traces when the provider exposes them.
- Adds a stats footer to each response with time to first token, total time, tokens per
  second, token counts, finish reason, and API cost.
- Saves every submitted round to local JSON files and builds a leaderboard from them.

## Guide

| Page | Read it when you want to |
| --- | --- |
| [Setup](setup.md) | Install, add an API key, and start the app |
| [Using the arena](using-the-arena.md) | Understand a round, voting, reasoning controls, and the leaderboard |
| [Data files](data.md) | Know what the app writes to disk and how to reset it |
| [Architecture](architecture.md) | Find your way around the code |
| [Development](development.md) | Run tests, understand CI, or bring the project back to life |
