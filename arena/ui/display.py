from typing import Any

import gradio as gr

from arena.state.round import (
	_default_display_order,
	_display_order_from_state,
	_empty_round_state,
	_panel_label,
	_slot_logs_from_state,
)
from arena.ui.config import PANEL_COUNT


def _chatbot_config(**kwargs: Any) -> gr.Chatbot:
	return gr.Chatbot(type="messages", group_consecutive_messages=False, **kwargs)


def _chatbot_panel_label(display_index: int, round_state: dict[str, Any] | None = None) -> str:
	if not isinstance(round_state, dict) or not round_state.get("submitted"):
		return _panel_label(display_index)

	display_order = _display_order_from_state(round_state)
	slot_logs = _slot_logs_from_state(round_state)
	if display_index >= len(display_order):
		return _panel_label(display_index)

	slot = display_order[display_index]
	if slot >= len(slot_logs):
		return _panel_label(display_index)

	model_label = str(slot_logs[slot].get("model_label") or "").strip()
	return model_label or _panel_label(display_index)


def _message_text_content(message: Any) -> str:
	if isinstance(message, gr.ChatMessage):
		return message.content if isinstance(message.content, str) else ""
	if isinstance(message, dict):
		content = message.get("content")
		return content if isinstance(content, str) else ""
	return ""


def _serialize_message(message: Any) -> dict[str, Any]:
	if isinstance(message, gr.ChatMessage):
		serialized = {
			"role": message.role,
			"content": message.content if isinstance(message.content, str) else "",
		}
		if isinstance(message.metadata, dict) and message.metadata:
			serialized["metadata"] = dict(message.metadata)
		return serialized

	if isinstance(message, dict):
		serialized = {
			"role": str(message.get("role") or "assistant"),
			"content": _message_text_content(message),
		}
		metadata = message.get("metadata")
		if isinstance(metadata, dict) and metadata:
			serialized["metadata"] = dict(metadata)
		return serialized

	return {"role": "assistant", "content": str(message)}


def _serialize_history(history: list[Any]) -> list[dict[str, Any]]:
	return [_serialize_message(message) for message in history]


def _finalize_round_state_logs(
	round_state: dict[str, Any],
	histories: list[list[Any]],
	reasoning_message_indices: list[int | None],
) -> None:
	slot_logs = round_state.get("slot_logs")
	if not isinstance(slot_logs, list):
		return

	for slot in range(min(PANEL_COUNT, len(slot_logs), len(histories))):
		history = histories[slot]
		slot_log = slot_logs[slot]
		slot_log["message_history"] = _serialize_history(history)
		reasoning_index = reasoning_message_indices[slot]
		if isinstance(reasoning_index, int) and 0 <= reasoning_index < len(history):
			slot_log["reasoning_trace"] = _message_text_content(history[reasoning_index]).strip()


def _format_duration(value: float | None) -> str:
	if value is None or value < 0:
		return "n/a"
	return f"{value:.2f}s"


def _format_tokens_per_second(value: float | None) -> str:
	if value is None or value < 0:
		return "n/a"
	return f"{value:.1f} tok/s"


def _format_cost(value: float | None) -> str:
	if value is None or value < 0:
		return "n/a"
	if value >= 0.01:
		return f"${value:.4f}"
	if value >= 0.0001:
		return f"${value:.6f}"
	return f"${value:.8f}"


def _extract_reasoning_tokens(usage: dict[str, Any] | None) -> int | None:
	if not isinstance(usage, dict):
		return None

	completion_details = usage.get("completion_tokens_details")
	if not isinstance(completion_details, dict):
		return None

	reasoning_tokens = completion_details.get("reasoning_tokens")
	try:
		return int(reasoning_tokens) if reasoning_tokens is not None else None
	except (TypeError, ValueError):
		return None


def _reasoning_trace_title(
	usage: dict[str, Any] | None = None,
	unavailable: bool = False,
) -> str:
	if unavailable:
		return "🔎 Reasoning Trace (not exposed)"

	reasoning_tokens = _extract_reasoning_tokens(usage)
	if reasoning_tokens is None:
		return "🔎 Reasoning Trace"

	token_label = "token" if reasoning_tokens == 1 else "tokens"
	return f"🔎 Reasoning Trace · {reasoning_tokens} {token_label}"


def _format_reasoning_details(reasoning_details: list[dict[str, Any]]) -> str:
	def _append_section(sections: list[str], section: str) -> None:
		if section and (not sections or sections[-1] != section):
			sections.append(section)

	has_summary = any(
		isinstance(detail, dict)
		and detail.get("type") == "reasoning.summary"
		and str(detail.get("summary") or "").strip()
		for detail in reasoning_details
	)

	sections: list[str] = []
	text_chunks: list[str] = []
	text_insert_index: int | None = None

	for detail in reasoning_details:
		if not isinstance(detail, dict):
			continue

		detail_type = detail.get("type")
		if detail_type == "reasoning.summary":
			summary = str(detail.get("summary") or "").strip()
			if summary:
				_append_section(sections, f"**Summary**\n\n{summary}")
			continue

		if detail_type == "reasoning.text":
			text = str(detail.get("text") or "").strip()
			if not text or has_summary:
				continue
			if text_insert_index is None:
				text_insert_index = len(sections)
			if not text_chunks or text_chunks[-1] != text:
				text_chunks.append(text)
			continue

		if detail_type == "reasoning.encrypted":
			payload = str(detail.get("data") or "").strip()
			if "[REDACTED]" in payload:
				_append_section(sections, "_Reasoning block was redacted by the provider._")
			else:
				_append_section(
					sections, "_Reasoning block was returned in encrypted form by the provider._"
				)
			continue

		_append_section(sections, "_Received an unsupported reasoning block from the provider._")

	if text_chunks:
		merged_text = "".join(chunk for chunk in text_chunks if chunk).strip()
		if merged_text:
			if text_insert_index is None:
				_append_section(sections, merged_text)
			elif text_insert_index == 0 or sections[text_insert_index - 1] != merged_text:
				sections.insert(text_insert_index, merged_text)

	return "\n\n".join(section for section in sections if section).strip()


def _upsert_assistant_message(
	history: list[Any],
	message_index: int | None,
	content: str,
	append: bool = False,
) -> int:
	if message_index is None:
		history.append({"role": "assistant", "content": content})
		return len(history) - 1

	current_content = _message_text_content(history[message_index])
	history[message_index] = {
		"role": "assistant",
		"content": f"{current_content}{content}" if append else content,
	}
	return message_index


def _upsert_reasoning_message(
	history: list[Any],
	message_index: int | None,
	slot: int,
	content: str,
	usage: dict[str, Any] | None = None,
	pending: bool = True,
	unavailable: bool = False,
	assistant_message_index: int | None = None,
) -> tuple[int, int | None]:
	reasoning_message = gr.ChatMessage(
		role="assistant",
		content=content,
		metadata={
			"title": _reasoning_trace_title(usage, unavailable=unavailable),
			"id": f"reasoning-{slot}",
			"status": "pending" if pending else "done",
		},
	)

	if message_index is None:
		if assistant_message_index is None:
			history.append(reasoning_message)
			return len(history) - 1, assistant_message_index

		history.insert(assistant_message_index, reasoning_message)
		return assistant_message_index, assistant_message_index + 1

	history[message_index] = reasoning_message
	return message_index, assistant_message_index


def _stats_footer(history: list[Any], chunk: dict[str, Any]) -> list[Any]:
	stats = chunk.get("stats") or {}
	usage = chunk.get("usage") or {}
	cost = usage.get("cost") if isinstance(usage, dict) else None
	completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
	reasoning_tokens = _extract_reasoning_tokens(usage if isinstance(usage, dict) else None)
	finish_reason = stats.get("finish_reason") or "n/a"
	completion_tokens_label = str(completion_tokens) if completion_tokens is not None else "n/a"
	reasoning_tokens_label = str(reasoning_tokens) if reasoning_tokens is not None else "n/a"

	history.append(
		gr.ChatMessage(
			role="assistant",
			content=(
				f"TTFT: {_format_duration(stats.get('time_to_first_token'))}\n"
				f"Total time: {_format_duration(stats.get('total_generation_time'))}\n"
				f"Tokens/sec: {_format_tokens_per_second(stats.get('tokens_per_second'))}\n"
				f"Completion tokens: {completion_tokens_label}\n"
				f"Reasoning tokens: {reasoning_tokens_label}\n"
				f"Finish reason: {finish_reason}\n"
				f"API cost: {_format_cost(cost)}"
			),
			metadata={"title": "🛠️ Generation Stats", "status": "done"},
		)
	)

	return history


def _chatbot_updates(
	histories: list[list[Any]],
	display_order: list[int],
	round_state: dict[str, Any] | None = None,
) -> tuple[gr.Chatbot, gr.Chatbot, gr.Chatbot]:
	return tuple(
		_chatbot_config(
			value=histories[display_order[index]],
			label=_chatbot_panel_label(index, round_state),
		)
		for index in range(PANEL_COUNT)
	)


def _chatbot_values(
	histories: list[list[Any]],
	display_order: list[int],
) -> tuple[Any, Any, Any]:
	return tuple(histories[display_order[index]] for index in range(PANEL_COUNT))


def _targeted_chatbot_value_updates(
	histories: list[list[Any]],
	display_order: list[int],
	slot: int | None = None,
	update_all: bool = False,
) -> tuple[Any, Any, Any]:
	if update_all:
		return _chatbot_values(histories, display_order)

	updates: list[Any] = [gr.skip() for _ in range(PANEL_COUNT)]
	if slot is None:
		return tuple(updates)

	try:
		display_index = display_order.index(slot)
	except ValueError:
		return tuple(updates)

	updates[display_index] = histories[slot]
	return tuple(updates)


def _skip_vote_updates() -> tuple[Any, Any, Any, Any, Any, Any]:
	return tuple(gr.skip() for _ in range(6))


def _streaming_outputs(
	*,
	user_input: Any | None = None,
	chatbot_updates: tuple[Any, Any, Any] | None = None,
	round_state: dict[str, Any] | None = None,
	vote_updates: tuple[Any, Any, Any, Any, Any, Any] | None = None,
) -> tuple[Any, ...]:
	return (
		gr.skip() if user_input is None else user_input,
		*(
			chatbot_updates
			if chatbot_updates is not None
			else _targeted_chatbot_value_updates([], _default_display_order())
		),
		round_state if round_state is not None else gr.skip(),
		*(vote_updates if vote_updates is not None else _skip_vote_updates()),
	)


def _chatbot_histories_from_state(round_state: dict[str, Any] | None) -> list[list[Any]]:
	slot_logs = _slot_logs_from_state(round_state)
	histories: list[list[Any]] = []
	for slot in range(PANEL_COUNT):
		if slot >= len(slot_logs):
			histories.append([])
			continue
		message_history = slot_logs[slot].get("message_history")
		if not isinstance(message_history, list):
			histories.append([])
			continue
		histories.append(
			[dict(message) if isinstance(message, dict) else message for message in message_history]
		)
	return histories


def _leaderboard_rows(model_totals: dict[str, Any] | None) -> list[list[Any]]:
	if not isinstance(model_totals, dict):
		return []

	leaderboard_entries: list[dict[str, Any]] = []
	for model_id, raw_entry in model_totals.items():
		if not isinstance(raw_entry, dict):
			continue

		wins = int(raw_entry.get("wins") or 0)
		appearances = int(raw_entry.get("appearances") or 0)
		provider_key = str(raw_entry.get("provider_key") or "").strip() or "unknown"
		model_label = (
			str(raw_entry.get("model_label") or model_id or "Unknown model").strip()
			or "Unknown model"
		)
		win_rate = (wins / appearances) if appearances else 0.0

		leaderboard_entries.append(
			{
				"model_label": model_label,
				"provider_key": provider_key,
				"wins": wins,
				"appearances": appearances,
				"win_rate": win_rate,
			}
		)

	leaderboard_entries.sort(
		key=lambda entry: (
			-entry["wins"],
			-entry["appearances"],
			entry["model_label"].lower(),
		)
	)

	return [
		[
			rank,
			entry["model_label"],
			entry["provider_key"],
			entry["wins"],
			entry["appearances"],
			f"{entry['win_rate']:.0%}",
		]
		for rank, entry in enumerate(leaderboard_entries, start=1)
	]


def _leaderboard_summary(total_rounds: Any, leaderboard_rows: list[list[Any]]) -> str:
	row_count = len(leaderboard_rows)
	rounds = int(total_rounds or 0)
	if not row_count:
		return "## Leaderboard\n\nNo submitted votes yet. Complete a round in the Arena tab to populate the standings."

	rounds_label = "round" if rounds == 1 else "rounds"
	models_label = "model" if row_count == 1 else "models"
	return (
		"## Leaderboard\n\n"
		f"{rounds} submitted {rounds_label} across {row_count} ranked {models_label}. "
		"Sorted by total wins."
	)


def _empty_display_state() -> dict[str, Any]:
	return _empty_round_state()
