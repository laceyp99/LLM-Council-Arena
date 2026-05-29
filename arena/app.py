import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import gradio as gr

from arena.core.api import OpenRouterAPI
from arena.core.models import (
	_build_provider_index,
	_default_model_ids,
	_load_model_catalog,
)
from arena.core.models import (
	_chatbot_label as _catalog_chatbot_label,
)
from arena.core.models import (
	_model_choices_for_provider as _catalog_model_choices_for_provider,
)
from arena.core.models import (
	_provider_for_model as _catalog_provider_for_model,
)
from arena.core.models import (
	_resolve_model_for_provider as _catalog_resolve_model_for_provider,
)
from arena.state.round import (
	_build_round_state,
	_default_display_order,
	_display_order_from_state,
	_empty_round_state,
	_panel_label,
	_shuffled_display_order,
	_slot_logs_from_state,
)
from arena.state.voting import (
	_display_mapping_from_state,
	_ranking_choices_from_state,
	_ranking_details_from_state,
	_vote_ui_updates,
	reset_vote,
	vote_response_a,
	vote_response_b,
	vote_response_c,
)
from arena.ui.config import (
	APP_DIR,
	DEFAULT_MODEL_IDS,
	DEFAULT_REASONING_SETTINGS,
	DEFAULT_SYSTEM_PROMPT,
	LOGS_DIR,
	META_LOG_FILE,
	PANEL_COUNT,
	SESSION_LOGS_DIR,
	SITE_NAME,
	SITE_URL,
	VOTES_FILE,
)
from arena.ui.display import (
	_chatbot_config,
	_chatbot_histories_from_state,
	_chatbot_updates,
	_extract_reasoning_tokens,
	_finalize_round_state_logs,
	_format_reasoning_details,
	_leaderboard_rows,
	_leaderboard_summary,
	_message_text_content,
	_stats_footer,
	_streaming_outputs,
	_targeted_chatbot_value_updates,
	_upsert_assistant_message,
	_upsert_reasoning_message,
)

MODEL_CATALOG, MODEL_CATALOG_STATUS, OPENROUTER_API_KEY = _load_model_catalog(
	site_url=SITE_URL,
	site_name=SITE_NAME,
)
MODEL_LOOKUP = {entry["model_id"]: entry for entry in MODEL_CATALOG}
PROVIDER_CHOICES, PROVIDER_MODELS = _build_provider_index(MODEL_CATALOG)
DEFAULT_PANEL_MODEL_IDS = _default_model_ids(
	model_catalog=MODEL_CATALOG,
	provider_models=PROVIDER_MODELS,
	default_model_ids=DEFAULT_MODEL_IDS,
	panel_count=PANEL_COUNT,
)


def _timestamp_slug(iso_timestamp: str | None) -> str:
	if not iso_timestamp:
		return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

	try:
		parsed_timestamp = datetime.fromisoformat(iso_timestamp)
	except ValueError:
		return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

	return parsed_timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _empty_meta_log() -> dict[str, Any]:
	return {
		"schema_version": 1,
		"updated_at": None,
		"total_rounds": 0,
		"model_totals": {},
		"round_summaries": [],
	}


def _write_json_file(path: Path, payload: Any) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def _read_json_file(path: Path, expected_type: type[Any], missing_default: Any) -> Any:
	if not path.exists():
		return missing_default

	try:
		payload = json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as exc:
		raise RuntimeError(f"{path.name} is not valid JSON.") from exc

	if not isinstance(payload, expected_type):
		raise RuntimeError(f"{path.name} has an unexpected JSON structure.")

	return payload


def _ensure_votes_file() -> None:
	if VOTES_FILE.exists():
		return

	VOTES_FILE.write_text("[]\n", encoding="utf-8")


def _ensure_log_store() -> None:
	LOGS_DIR.mkdir(parents=True, exist_ok=True)
	SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
	if not META_LOG_FILE.exists():
		_write_json_file(META_LOG_FILE, _empty_meta_log())


def _bootstrap_persistence() -> None:
	_ensure_votes_file()
	_ensure_log_store()


def _append_vote_record(record: dict[str, Any]) -> None:
	_ensure_votes_file()

	existing_records = _read_json_file(VOTES_FILE, list, [])

	existing_records.append(record)
	_write_json_file(VOTES_FILE, existing_records)


def _build_vote_record(
	round_state: dict[str, Any], session_dir: str | None = None
) -> dict[str, Any]:
	return {
		"timestamp": round_state.get("submitted_at") or datetime.now(timezone.utc).isoformat(),
		"round_id": round_state.get("round_id"),
		"prompt_text": round_state.get("user_text") or "",
		"prompt_sha256": round_state.get("prompt_sha256") or "",
		"system_prompt": round_state.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
		"selected_models": [
			{
				"selection_slot": slot_log.get("selection_slot"),
				"response_label": slot_log.get("response_label") or "",
				"model_id": slot_log.get("model_id") or "",
				"model_label": slot_log.get("model_label") or "",
			}
			for slot_log in _slot_logs_from_state(round_state)
		],
		"display_order": _display_mapping_from_state(round_state),
		"ranking": _ranking_details_from_state(round_state),
		"session_dir": session_dir,
	}


def _build_round_payload(round_state: dict[str, Any]) -> dict[str, Any]:
	return {
		"schema_version": 1,
		"round_id": round_state.get("round_id"),
		"created_at": round_state.get("created_at"),
		"generation_completed_at": round_state.get("generation_completed_at"),
		"submitted_at": round_state.get("submitted_at"),
		"prompt": {
			"text": round_state.get("user_text") or "",
			"sha256": round_state.get("prompt_sha256") or "",
			"system_prompt": round_state.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
		},
		"message_payload": round_state.get("messages_payload") or [],
		"display_mapping": _display_mapping_from_state(round_state),
		"completed_slots": round_state.get("completed_slots") or [],
		"errored_slots": round_state.get("errored_slots") or [],
	}


def _build_histories_payload(round_state: dict[str, Any]) -> dict[str, Any]:
	return {
		"schema_version": 1,
		"round_id": round_state.get("round_id"),
		"panels": [
			{
				"selection_slot": slot_log.get("selection_slot"),
				"response_label": slot_log.get("response_label") or "",
				"model_id": slot_log.get("model_id") or "",
				"model_label": slot_log.get("model_label") or "",
				"message_history": slot_log.get("message_history") or [],
			}
			for slot_log in _slot_logs_from_state(round_state)
		],
	}


def _build_generation_payload(round_state: dict[str, Any]) -> dict[str, Any]:
	return {
		"schema_version": 1,
		"round_id": round_state.get("round_id"),
		"panels": [
			{
				"selection_slot": slot_log.get("selection_slot"),
				"response_label": slot_log.get("response_label") or "",
				"model_id": slot_log.get("model_id") or "",
				"model_label": slot_log.get("model_label") or "",
				"provider_key": slot_log.get("provider_key") or "",
				"status": slot_log.get("status") or "pending",
				"error": slot_log.get("error"),
				"final_response": slot_log.get("final_response") or "",
				"reasoning_trace": slot_log.get("reasoning_trace") or "",
				"reasoning_details": slot_log.get("reasoning_details") or [],
				"usage": slot_log.get("usage") or {},
				"stats": slot_log.get("stats") or {},
				"finish_reason": slot_log.get("finish_reason"),
				"cost": slot_log.get("cost"),
				"completion_tokens": slot_log.get("completion_tokens"),
				"reasoning_tokens": slot_log.get("reasoning_tokens"),
			}
			for slot_log in _slot_logs_from_state(round_state)
		],
	}


def _update_meta_log(round_state: dict[str, Any], session_dir: str) -> None:
	_ensure_log_store()
	meta_log = _read_json_file(META_LOG_FILE, dict, _empty_meta_log())
	model_totals = meta_log.setdefault("model_totals", {})

	for slot_log in _slot_logs_from_state(round_state):
		model_id = str(slot_log.get("model_id") or "")
		if not model_id:
			continue

		model_entry = model_totals.setdefault(
			model_id,
			{
				"model_label": slot_log.get("model_label") or model_id,
				"provider_key": slot_log.get("provider_key") or "",
				"appearances": 0,
				"rank_1_count": 0,
				"rank_2_count": 0,
				"rank_3_count": 0,
				"wins": 0,
				"error_count": 0,
				"completion_count": 0,
				"total_completion_tokens": 0,
				"total_reasoning_tokens": 0,
				"total_cost": 0.0,
			},
		)

		model_entry["model_label"] = slot_log.get("model_label") or model_entry["model_label"]
		model_entry["provider_key"] = slot_log.get("provider_key") or model_entry["provider_key"]
		model_entry["appearances"] += 1
		if slot_log.get("error"):
			model_entry["error_count"] += 1
		if slot_log.get("status") == "complete":
			model_entry["completion_count"] += 1
		completion_tokens = slot_log.get("completion_tokens")
		if isinstance(completion_tokens, (int, float)):
			model_entry["total_completion_tokens"] += int(completion_tokens)
		reasoning_tokens = slot_log.get("reasoning_tokens")
		if isinstance(reasoning_tokens, (int, float)):
			model_entry["total_reasoning_tokens"] += int(reasoning_tokens)
		cost = slot_log.get("cost")
		if isinstance(cost, (int, float)):
			model_entry["total_cost"] = round(model_entry["total_cost"] + float(cost), 8)

	for ranking_entry in _ranking_details_from_state(round_state):
		model_id = str(ranking_entry.get("model_id") or "")
		if not model_id or model_id not in model_totals:
			continue
		rank = int(ranking_entry.get("rank") or 0)
		if 1 <= rank <= PANEL_COUNT:
			model_totals[model_id][f"rank_{rank}_count"] += 1
			if rank == 1:
				model_totals[model_id]["wins"] += 1

	meta_log["updated_at"] = datetime.now(timezone.utc).isoformat()
	meta_log["total_rounds"] = int(meta_log.get("total_rounds") or 0) + 1
	meta_log.setdefault("round_summaries", []).append(
		{
			"round_id": round_state.get("round_id"),
			"created_at": round_state.get("created_at"),
			"submitted_at": round_state.get("submitted_at"),
			"prompt_sha256": round_state.get("prompt_sha256") or "",
			"session_dir": session_dir,
			"selected_models": [
				{
					"selection_slot": slot_log.get("selection_slot"),
					"model_id": slot_log.get("model_id") or "",
					"model_label": slot_log.get("model_label") or "",
				}
				for slot_log in _slot_logs_from_state(round_state)
			],
			"ranking": _ranking_details_from_state(round_state),
			"errored_slots": round_state.get("errored_slots") or [],
		},
	)

	_write_json_file(META_LOG_FILE, meta_log)


def _write_round_logs(round_state: dict[str, Any]) -> str:
	_ensure_log_store()
	round_id = str(round_state.get("round_id") or uuid4().hex)
	session_dir_name = f"{_timestamp_slug(str(round_state.get('submitted_at') or ''))}_{round_id}"
	session_dir = SESSION_LOGS_DIR / session_dir_name
	session_dir.mkdir(parents=True, exist_ok=True)
	session_dir_relative = session_dir.relative_to(APP_DIR).as_posix()

	try:
		_write_json_file(session_dir / "round.json", _build_round_payload(round_state))
		_write_json_file(
			session_dir / "vote.json",
			{
				"schema_version": 1,
				"round_id": round_id,
				"submitted_at": round_state.get("submitted_at"),
				"vote_sequence": {
					"first_choice": round_state.get("first_choice"),
					"second_choice": round_state.get("second_choice"),
					"third_choice": round_state.get("third_choice"),
				},
				"display_mapping": _display_mapping_from_state(round_state),
				"ranking": _ranking_details_from_state(round_state),
			},
		)
		_write_json_file(session_dir / "histories.json", _build_histories_payload(round_state))
		_write_json_file(session_dir / "generation.json", _build_generation_payload(round_state))
		_update_meta_log(round_state, session_dir_relative)
	except Exception:
		shutil.rmtree(session_dir, ignore_errors=True)
		raise
	return session_dir_relative


def _chatbot_label(model_id: str) -> str:
	return _catalog_chatbot_label(MODEL_LOOKUP, model_id)


def _provider_for_model(model_id: str) -> str:
	return _catalog_provider_for_model(MODEL_LOOKUP, PROVIDER_CHOICES, model_id)


def _model_choices_for_provider(provider_key: str) -> list[tuple[str, str]]:
	return _catalog_model_choices_for_provider(PROVIDER_MODELS, MODEL_CATALOG, provider_key)


def _resolve_model_for_provider(provider_key: str, model_id: str | None = None) -> str:
	return _catalog_resolve_model_for_provider(
		PROVIDER_MODELS, MODEL_CATALOG, provider_key, model_id
	)


def _build_messages(user_text: str, system_prompt: str) -> list[dict[str, str]]:
	resolved_system_prompt = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
	return [
		{"role": "system", "content": resolved_system_prompt},
		{"role": "user", "content": user_text},
	]


def _reset_arena_outputs() -> tuple[Any, ...]:
	empty_histories = [[] for _ in range(PANEL_COUNT)]
	empty_state = _empty_round_state()
	return (
		*_chatbot_updates(empty_histories, _default_display_order()),
		empty_state,
		*_vote_ui_updates(empty_state),
	)


def _leaderboard_view_data() -> tuple[str, list[list[Any]]]:
	try:
		meta_log = _read_json_file(META_LOG_FILE, dict, _empty_meta_log())
	except RuntimeError as exc:
		return f"## Leaderboard\n\nLeaderboard unavailable: {exc}", []

	leaderboard_rows = _leaderboard_rows(meta_log.get("model_totals"))
	return _leaderboard_summary(meta_log.get("total_rounds"), leaderboard_rows), leaderboard_rows


def _submit_vote_outputs(
	round_state: dict[str, Any] | None,
) -> tuple[Any, ...]:
	current_state = round_state if isinstance(round_state, dict) else _empty_round_state()
	leaderboard_summary, leaderboard_rows = _leaderboard_view_data()
	return (
		current_state,
		*_chatbot_updates(
			_chatbot_histories_from_state(current_state),
			_display_order_from_state(current_state),
			current_state,
		),
		*_vote_ui_updates(current_state),
		leaderboard_summary,
		leaderboard_rows,
	)


def update_panel_provider(provider_key: str):
	resolved_model_id = _resolve_model_for_provider(provider_key)
	return (
		gr.Dropdown(choices=_model_choices_for_provider(provider_key), value=resolved_model_id),
		*_reset_arena_outputs(),
	)


def update_panel_model(model_id: str):
	_ = model_id
	return _reset_arena_outputs()


def submit_vote(round_state: dict[str, Any] | None):
	current_state = round_state if isinstance(round_state, dict) else _empty_round_state()

	if not current_state.get("round_id"):
		return _submit_vote_outputs(current_state)
	if current_state.get("submitted"):
		return _submit_vote_outputs(current_state)
	if not current_state.get("ready_for_vote"):
		return _submit_vote_outputs(current_state)

	first_choice, second_choice, third_choice = _ranking_choices_from_state(current_state)
	if not all((first_choice, second_choice, third_choice)):
		return _submit_vote_outputs(current_state)

	candidate_state = {
		**current_state,
		"submitted": True,
		"submitted_at": datetime.now(timezone.utc).isoformat(),
		"vote_stage": "submitted",
		"log_warning": None,
	}

	try:
		session_dir = _write_round_logs(candidate_state)
	except RuntimeError:
		return _submit_vote_outputs(current_state)

	candidate_state["session_dir"] = session_dir

	try:
		_append_vote_record(_build_vote_record(candidate_state, session_dir))
	except RuntimeError as exc:
		candidate_state["log_warning"] = str(exc)

	return _submit_vote_outputs(candidate_state)


def _apply_stream_chunk(
	round_state: dict[str, Any],
	histories: list[list[Any]],
	assistant_message_indices: list[int | None],
	reasoning_message_indices: list[int | None],
	completed_slots: set[int],
	errored_slots: set[int],
	chunk: dict[str, Any],
) -> int | None:
	slot = chunk.get("slot")
	if not isinstance(slot, int) or not (0 <= slot < PANEL_COUNT):
		return None

	changed_history = False
	if chunk.get("event") == "error" or chunk.get("error"):
		errored_slots.add(slot)
		round_state["slot_logs"][slot]["status"] = "error"
		round_state["slot_logs"][slot]["error"] = str(chunk.get("error") or "Unknown error")
		error_prefix = "\n" if assistant_message_indices[slot] is not None else ""
		assistant_message_indices[slot] = _upsert_assistant_message(
			history=histories[slot],
			message_index=assistant_message_indices[slot],
			content=f"{error_prefix}[Error] {round_state['slot_logs'][slot]['error']}",
			append=True,
		)
		reasoning_index = reasoning_message_indices[slot]
		if reasoning_index is not None:
			reasoning_content = _message_text_content(histories[slot][reasoning_index]).strip()
			if not reasoning_content:
				reasoning_content = "_Reasoning trace interrupted by an upstream error._"
			reasoning_message_indices[slot], assistant_message_indices[slot] = (
				_upsert_reasoning_message(
					history=histories[slot],
					message_index=reasoning_index,
					slot=slot,
					content=reasoning_content,
					usage=chunk.get("usage"),
					pending=False,
					assistant_message_index=assistant_message_indices[slot],
				)
			)
		changed_history = True
	elif chunk.get("event") == "reasoning":
		reasoning_content = _format_reasoning_details(chunk.get("reasoning_details") or [])
		if reasoning_content:
			round_state["slot_logs"][slot]["status"] = "streaming"
			round_state["slot_logs"][slot]["reasoning_trace"] = reasoning_content
			round_state["slot_logs"][slot]["reasoning_details"] = [
				dict(detail)
				for detail in (chunk.get("reasoning_details") or [])
				if isinstance(detail, dict)
			]
			reasoning_message_indices[slot], assistant_message_indices[slot] = (
				_upsert_reasoning_message(
					history=histories[slot],
					message_index=reasoning_message_indices[slot],
					slot=slot,
					content=reasoning_content,
					usage=chunk.get("usage"),
					pending=True,
					assistant_message_index=assistant_message_indices[slot],
				)
			)
			changed_history = True
	elif chunk.get("event") == "complete":
		completed_slots.add(slot)
		usage = chunk.get("usage") if isinstance(chunk.get("usage"), dict) else {}
		stats = chunk.get("stats") if isinstance(chunk.get("stats"), dict) else {}
		round_state["slot_logs"][slot]["status"] = "complete"
		round_state["slot_logs"][slot]["usage"] = dict(usage)
		round_state["slot_logs"][slot]["stats"] = dict(stats)
		round_state["slot_logs"][slot]["finish_reason"] = stats.get("finish_reason")
		round_state["slot_logs"][slot]["cost"] = usage.get("cost")
		round_state["slot_logs"][slot]["completion_tokens"] = usage.get("completion_tokens")
		round_state["slot_logs"][slot]["reasoning_tokens"] = _extract_reasoning_tokens(usage)
		reasoning_index = reasoning_message_indices[slot]
		normalized_reasoning_details = [
			dict(detail)
			for detail in (chunk.get("reasoning_details") or [])
			if isinstance(detail, dict)
		]
		if normalized_reasoning_details:
			round_state["slot_logs"][slot]["reasoning_details"] = normalized_reasoning_details
		reasoning_content = _format_reasoning_details(normalized_reasoning_details)
		if reasoning_index is not None:
			existing_reasoning_content = _message_text_content(
				histories[slot][reasoning_index]
			).strip()
			final_reasoning_content = (
				existing_reasoning_content
				or reasoning_content
				or "_No reasoning trace was exposed in the final stream payload._"
			)
			reasoning_message_indices[slot], assistant_message_indices[slot] = (
				_upsert_reasoning_message(
					history=histories[slot],
					message_index=reasoning_index,
					slot=slot,
					content=final_reasoning_content,
					usage=chunk.get("usage"),
					pending=False,
					assistant_message_index=assistant_message_indices[slot],
				)
			)
		elif reasoning_content:
			reasoning_message_indices[slot], assistant_message_indices[slot] = (
				_upsert_reasoning_message(
					history=histories[slot],
					message_index=None,
					slot=slot,
					content=reasoning_content,
					usage=chunk.get("usage"),
					pending=False,
					assistant_message_index=assistant_message_indices[slot],
				)
			)
		elif _extract_reasoning_tokens(chunk.get("usage")):
			reasoning_message_indices[slot], assistant_message_indices[slot] = (
				_upsert_reasoning_message(
					history=histories[slot],
					message_index=None,
					slot=slot,
					content=(
						"This model reported reasoning token usage, but it did not expose a "
						"reasoning trace in the OpenRouter stream for this response."
					),
					usage=chunk.get("usage"),
					pending=False,
					unavailable=True,
					assistant_message_index=assistant_message_indices[slot],
				)
			)
		histories[slot] = _stats_footer(histories[slot], chunk)
		changed_history = True
	else:
		delta = chunk.get("delta", "")
		if delta:
			round_state["slot_logs"][slot]["status"] = "streaming"
			round_state["slot_logs"][slot]["final_response"] = (
				f"{round_state['slot_logs'][slot]['final_response']}{delta}"
			)
			assistant_message_indices[slot] = _upsert_assistant_message(
				history=histories[slot],
				message_index=assistant_message_indices[slot],
				content=delta,
				append=True,
			)
			changed_history = True

	if not changed_history and chunk.get("event") not in {"complete", "error"}:
		return None

	_finalize_round_state_logs(
		round_state, histories, assistant_message_indices, reasoning_message_indices
	)
	return slot


def _finalize_generation_state(
	round_state: dict[str, Any],
	histories: list[list[Any]],
	assistant_message_indices: list[int | None],
	reasoning_message_indices: list[int | None],
	completed_slots: set[int],
	errored_slots: set[int],
) -> None:
	_finalize_round_state_logs(
		round_state, histories, assistant_message_indices, reasoning_message_indices
	)
	round_state["completed_slots"] = sorted(completed_slots)
	round_state["errored_slots"] = sorted(errored_slots)
	round_state["generation_completed_at"] = datetime.now(timezone.utc).isoformat()
	round_state["ready_for_vote"] = len(completed_slots) + len(
		errored_slots
	) == PANEL_COUNT and bool(completed_slots)
	round_state["vote_stage"] = "pick_first" if completed_slots else "unavailable"


async def stream_all_models(
	user_text: str,
	system_prompt: str,
	panel_1_model: str,
	panel_2_model: str,
	panel_3_model: str,
):
	user_text = (user_text or "").strip()
	if not user_text:
		return

	model_ids = [panel_1_model, panel_2_model, panel_3_model]
	message_payload = _build_messages(user_text, system_prompt)
	display_order = _shuffled_display_order()
	round_state = _build_round_state(
		user_text,
		system_prompt,
		message_payload,
		model_ids,
		display_order,
		chatbot_label=_chatbot_label,
		provider_for_model=_provider_for_model,
	)
	histories = [
		[
			{"role": "user", "content": user_text},
		]
		for _ in range(PANEL_COUNT)
	]
	assistant_message_indices: list[int | None] = [None for _ in range(PANEL_COUNT)]
	reasoning_message_indices: list[int | None] = [None for _ in range(PANEL_COUNT)]
	completed_slots: set[int] = set()
	errored_slots: set[int] = set()

	yield _streaming_outputs(
		user_input="",
		chatbot_updates=_chatbot_updates(histories, display_order, round_state),
		round_state=round_state,
		vote_updates=_vote_ui_updates(round_state),
	)

	missing_selection = [index for index, model_id in enumerate(model_ids) if not model_id]
	if missing_selection:
		for index in missing_selection:
			assistant_message_indices[index] = _upsert_assistant_message(
				history=histories[index],
				message_index=assistant_message_indices[index],
				content="No model selected for this panel.",
			)
			round_state["slot_logs"][index]["status"] = "blocked"
			round_state["slot_logs"][index]["error"] = "No model selected for this panel."
		_finalize_round_state_logs(
			round_state, histories, assistant_message_indices, reasoning_message_indices
		)
		yield _streaming_outputs(
			chatbot_updates=_targeted_chatbot_value_updates(
				histories, display_order, update_all=True
			),
			round_state=round_state,
			vote_updates=_vote_ui_updates(round_state),
		)
		return

	if not OPENROUTER_API_KEY:
		for index in range(PANEL_COUNT):
			assistant_message_indices[index] = _upsert_assistant_message(
				history=histories[index],
				message_index=assistant_message_indices[index],
				content="Missing OPENROUTER_API_KEY in environment.",
			)
			round_state["slot_logs"][index]["status"] = "blocked"
			round_state["slot_logs"][index]["error"] = "Missing OPENROUTER_API_KEY in environment."
		_finalize_round_state_logs(
			round_state, histories, assistant_message_indices, reasoning_message_indices
		)
		yield _streaming_outputs(
			chatbot_updates=_targeted_chatbot_value_updates(
				histories, display_order, update_all=True
			),
			round_state=round_state,
			vote_updates=_vote_ui_updates(round_state),
		)
		return

	api = OpenRouterAPI(api_key=OPENROUTER_API_KEY, site_url=SITE_URL, site_name=SITE_NAME)
	prompt_requests = [
		{
			"slot": slot,
			"model": model_id,
		}
		for slot, model_id in enumerate(model_ids)
	]

	async for chunk in api.prompt_models_concurrent(
		prompt_requests,
		message_payload,
		reasoning=DEFAULT_REASONING_SETTINGS,
	):
		slot = _apply_stream_chunk(
			round_state,
			histories,
			assistant_message_indices,
			reasoning_message_indices,
			completed_slots,
			errored_slots,
			chunk,
		)
		if slot is None:
			continue
		yield _streaming_outputs(
			chatbot_updates=_targeted_chatbot_value_updates(histories, display_order, slot=slot),
			round_state=round_state,
		)

	_finalize_generation_state(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	)

	yield _streaming_outputs(
		round_state=round_state,
		vote_updates=_vote_ui_updates(round_state),
	)


def clear_histories(panel_1_model: str, panel_2_model: str, panel_3_model: str):
	_ = (panel_1_model, panel_2_model, panel_3_model)
	return "", *_reset_arena_outputs()


default_panel_providers = [_provider_for_model(model_id) for model_id in DEFAULT_PANEL_MODEL_IDS]
initial_leaderboard_summary, initial_leaderboard_rows = _leaderboard_view_data()


with gr.Blocks(title="LLM Council Arena") as demo:
	gr.Markdown("# LLM Council Arena")

	with gr.Tabs():
		with gr.Tab("Arena"):
			with gr.Row():
				with gr.Column(scale=1):
					panel_1_provider = gr.Dropdown(
						label="Provider",
						choices=PROVIDER_CHOICES,
						value=default_panel_providers[0],
					)
					panel_1_model = gr.Dropdown(
						label="Model",
						choices=_model_choices_for_provider(default_panel_providers[0]),
						value=DEFAULT_PANEL_MODEL_IDS[0],
					)

				with gr.Column(scale=1):
					panel_2_provider = gr.Dropdown(
						label="Provider",
						choices=PROVIDER_CHOICES,
						value=default_panel_providers[1],
					)
					panel_2_model = gr.Dropdown(
						label="Model",
						choices=_model_choices_for_provider(default_panel_providers[1]),
						value=DEFAULT_PANEL_MODEL_IDS[1],
					)

				with gr.Column(scale=1):
					panel_3_provider = gr.Dropdown(
						label="Provider",
						choices=PROVIDER_CHOICES,
						value=default_panel_providers[2],
					)
					panel_3_model = gr.Dropdown(
						label="Model",
						choices=_model_choices_for_provider(default_panel_providers[2]),
						value=DEFAULT_PANEL_MODEL_IDS[2],
					)

			with gr.Accordion("System Prompt", open=False):
				system_prompt = gr.Textbox(
					label="Instructions to all models",
					value=DEFAULT_SYSTEM_PROMPT,
					lines=4,
				)

			user_input = gr.Textbox(
				label="Prompt",
				placeholder="Type your prompt and click Send...",
				lines=3,
			)
			send_btn = gr.Button("Send")
			round_state = gr.State(_empty_round_state())

			with gr.Row():
				with gr.Column(scale=1):
					panel_1_chat = _chatbot_config(label=_panel_label(0), height=520)
				with gr.Column(scale=1):
					panel_2_chat = _chatbot_config(label=_panel_label(1), height=520)
				with gr.Column(scale=1):
					panel_3_chat = _chatbot_config(label=_panel_label(2), height=520)

			with gr.Group():
				gr.Markdown("## Vote on the Anonymous Responses")
				with gr.Row():
					vote_response_a_btn = gr.Button(_panel_label(0), interactive=False)
					vote_response_b_btn = gr.Button(_panel_label(1), interactive=False)
					vote_response_c_btn = gr.Button(_panel_label(2), interactive=False)
				with gr.Row():
					vote_reset_btn = gr.Button("Reset Vote", interactive=False)
					vote_submit_btn = gr.Button("Submit Vote", interactive=False)

		with gr.Tab("Leaderboard"):
			leaderboard_summary_md = gr.Markdown(initial_leaderboard_summary)
			leaderboard_table = gr.Dataframe(
				headers=["Rank", "Model", "Provider", "Wins", "Appearances", "Win Rate"],
				value=initial_leaderboard_rows,
				interactive=False,
				wrap=True,
			)

	submit_outputs = [
		user_input,
		panel_1_chat,
		panel_2_chat,
		panel_3_chat,
		round_state,
		vote_response_a_btn,
		vote_response_b_btn,
		vote_response_c_btn,
		vote_reset_btn,
		vote_submit_btn,
	]
	submit_inputs = [user_input, system_prompt, panel_1_model, panel_2_model, panel_3_model]

	panel_1_provider.change(
		fn=update_panel_provider,
		inputs=[panel_1_provider],
		outputs=[
			panel_1_model,
			panel_1_chat,
			panel_2_chat,
			panel_3_chat,
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	panel_2_provider.change(
		fn=update_panel_provider,
		inputs=[panel_2_provider],
		outputs=[
			panel_2_model,
			panel_1_chat,
			panel_2_chat,
			panel_3_chat,
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	panel_3_provider.change(
		fn=update_panel_provider,
		inputs=[panel_3_provider],
		outputs=[
			panel_3_model,
			panel_1_chat,
			panel_2_chat,
			panel_3_chat,
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)

	panel_1_model.change(
		fn=update_panel_model,
		inputs=[panel_1_model],
		outputs=[
			panel_1_chat,
			panel_2_chat,
			panel_3_chat,
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	panel_2_model.change(
		fn=update_panel_model,
		inputs=[panel_2_model],
		outputs=[
			panel_1_chat,
			panel_2_chat,
			panel_3_chat,
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	panel_3_model.change(
		fn=update_panel_model,
		inputs=[panel_3_model],
		outputs=[
			panel_1_chat,
			panel_2_chat,
			panel_3_chat,
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)

	user_input.submit(
		fn=stream_all_models,
		inputs=submit_inputs,
		outputs=submit_outputs,
		show_progress="hidden",
	)
	send_btn.click(
		fn=stream_all_models,
		inputs=submit_inputs,
		outputs=submit_outputs,
		show_progress="hidden",
	)
	vote_response_a_btn.click(
		fn=vote_response_a,
		inputs=[round_state],
		outputs=[
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	vote_response_b_btn.click(
		fn=vote_response_b,
		inputs=[round_state],
		outputs=[
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	vote_response_c_btn.click(
		fn=vote_response_c,
		inputs=[round_state],
		outputs=[
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	vote_reset_btn.click(
		fn=reset_vote,
		inputs=[round_state],
		outputs=[
			round_state,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
		],
	)
	vote_submit_btn.click(
		fn=submit_vote,
		inputs=[round_state],
		outputs=[
			round_state,
			panel_1_chat,
			panel_2_chat,
			panel_3_chat,
			vote_response_a_btn,
			vote_response_b_btn,
			vote_response_c_btn,
			vote_reset_btn,
			vote_submit_btn,
			leaderboard_summary_md,
			leaderboard_table,
		],
	)
	demo.load(
		fn=_leaderboard_view_data,
		inputs=None,
		outputs=[leaderboard_summary_md, leaderboard_table],
	)


if __name__ == "__main__":
	_bootstrap_persistence()
	demo.queue().launch()
