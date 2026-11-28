# Data files

**Nothing is saved unless you submit a vote.** Each submission adds one line to
`votes.jsonl`, writes one session folder, and updates the leaderboard totals. (Startup
only creates an empty `arena_logs/meta.json`.)

All paths are relative to the folder you start the app from, normally the repository
root, where they are git-ignored. Starting the app from another folder gives it a
separate, empty leaderboard there.

```text
votes.jsonl                     # one line per submitted round
arena_logs/
  meta.json                     # leaderboard totals
  sessions/
    20261127T153012Z_<round_id>/
      round.json                # prompt and panel mapping
      vote.json                 # your ranking
      generation.json           # outputs, errors, tokens, cost, reasoning
      histories.json            # the chat messages each panel displayed
```

`*.lock` files appear next to `votes.jsonl` and `meta.json` while the app runs. They are
safe to delete when the app is stopped.

!!! warning "Privacy"
    These files store your prompts, the system prompt, and the full model responses in
    plain text. API keys are never written. Treat the folder as private if your prompts
    are.

## `votes.jsonl`

One JSON object per line, appended in submission order.

```json
{
  "timestamp": "2026-11-27T15:30:12.481230+00:00",
  "round_id": "9f2c...",
  "prompt_text": "Explain recursion to a ten year old.",
  "prompt_sha256": "4b1e...",
  "system_prompt": "You are a helpful assistant.",
  "selected_models": [
    {"selection_slot": 0, "response_label": "Response B", "model_id": "openai/gpt-6.1-terra", "model_label": "OpenAI: GPT-6.1 Terra"}
  ],
  "display_order": [
    {"response_label": "Response A", "selection_slot": 2, "model_id": "...", "model_label": "...", "provider_key": "google"}
  ],
  "ranking": [
    {"rank": 1, "response_label": "Response B", "selection_slot": 0, "model_id": "openai/gpt-6.1-terra", "model_label": "...", "provider_key": "openai"}
  ],
  "session_dir": "arena_logs/sessions/20261127T153012Z_9f2c..."
}
```

`selected_models` and `display_order` each have three entries, and `ranking` has one entry
per place. They are shortened above.

- `selection_slot` is the column the model was picked in (0 is the left column).
- `response_label` is the anonymous panel it was shown in.
- `session_dir` points to the matching session folder.

Older versions stored votes as one JSON array in `votes.json`. On startup, if
`votes.jsonl` does not exist but `votes.json` does, the app copies the old records into
`votes.jsonl` and leaves `votes.json` untouched.

## `arena_logs/meta.json`

The leaderboard's source of truth. It is a running total, not a log.

```json
{
  "schema_version": 2,
  "updated_at": "2026-11-27T15:30:12.512004+00:00",
  "total_rounds": 42,
  "model_totals": {
    "anthropic/claude-sonnet-5.5": {
      "model_label": "Anthropic: Claude Sonnet 5.5",
      "provider_key": "anthropic",
      "appearances": 30,
      "rank_1_count": 14,
      "rank_2_count": 9,
      "rank_3_count": 7,
      "wins": 14,
      "error_count": 1,
      "completion_count": 29,
      "total_completion_tokens": 18240,
      "total_reasoning_tokens": 5120,
      "total_cost": 0.2731
    }
  }
}
```

Schema version 1 also kept a `round_summaries` list. The app removes it the next time it
updates the file, since the session folders already hold that detail.

## Session folders

Folder names are `<UTC submit time>_<round_id>`. Every file inside has
`"schema_version": 1` and the `round_id`.

| File | Contents |
| --- | --- |
| `round.json` | Timestamps (created, generation completed, submitted), prompt text and hash, system prompt, the exact `messages` sent, which model was in which panel, and which slots completed or errored |
| `vote.json` | The 1st, 2nd, and 3rd picks by panel label, the panel mapping, and the resolved ranking by model |
| `generation.json` | Per panel: status, error, final response, the reasoning payload sent, reasoning trace and raw details, OpenRouter `usage`, timing stats, finish reason, cost, and token counts |
| `histories.json` | Per panel: the chat messages as displayed, including stats and warning messages |

## How saves stay consistent

A submission touches three places, and the app treats them as one unit:

1. It takes a lock on `votes.jsonl` and on `meta.json` (waiting up to 5 seconds on
   Windows).
2. It appends the vote line, writes the session folder, and rewrites `meta.json`.
3. If any step fails, it truncates `votes.jsonl` back to its old length, restores the old
   `meta.json`, and deletes the session folder. The UI shows "Vote could not be saved".

JSON files are written to a temporary file first and then swapped in, so a crash cannot
leave a half-written `meta.json`. If a crash cuts off the last line of `votes.jsonl`, the
next vote starts on a new line instead of joining the broken one.

## Resetting or recovering

Stop the app first.

- **Start fresh:** delete `votes.jsonl` and the `arena_logs/` folder.
- **"Leaderboard unavailable" or every vote fails to save:** `meta.json` is probably not
  valid JSON (for example after a manual edit). Fix it by hand, or delete it to reset the
  leaderboard to zero. Votes and session folders are kept, but the app has no command to
  rebuild totals from them.
- **"Timed out waiting for file lock":** another copy of the app is probably running
  against the same folder. Close it.
