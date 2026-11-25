import json
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import gradio as gr

from arena.core.api import OpenRouterAPI
from arena.core.models import CatalogState, _fallback_model_catalog, _load_model_catalog
from arena.core.reasoning import (
	normalize_reasoning_payload,
	reasoning_capabilities_for_model,
	reasoning_cost_hint,
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
	vote_response,
)
from arena.ui.config import (
	APP_DIR,
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
	_status_message,
	_streaming_outputs,
	_targeted_chatbot_value_updates,
	_upsert_assistant_message,
	_upsert_reasoning_message,
)

# Fallback until initialize_model_catalog() runs; importing stays free of dotenv and network.
CATALOG = CatalogState(
	_fallback_model_catalog(),
	"Warning: model catalog has not been initialized. Using the fallback model list.",
)
FILE_LOCK_TIMEOUT_SECONDS = 5.0
FILE_LOCK_RETRY_INTERVAL_SECONDS = 0.01
LOGGER = logging.getLogger(__name__)


def initialize_model_catalog() -> None:
	global CATALOG
	CATALOG = _load_model_catalog(site_url=SITE_URL, site_name=SITE_NAME)


def _timestamp_slug(iso_timestamp: str | None) -> str:
	if not iso_timestamp:
		return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

	try:
		parsed_timestamp = datetime.fromisoformat(iso_timestamp)
	except ValueError:
		return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

	return parsed_timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


META_LOG_SCHEMA_VERSION = 2


def _empty_meta_log() -> dict[str, Any]:
	return {
		"schema_version": META_LOG_SCHEMA_VERSION,
		"updated_at": None,
		"total_rounds": 0,
		"model_totals": {},
	}


def _write_json_file(path: Path, payload: Any) -> None:
	_write_text_file(path, f"{json.dumps(payload, indent=2)}\n")


def _write_text_file(path: Path, text: str) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary_path: Path | None = None

	try:
		with tempfile.NamedTemporaryFile(
			"w",
			delete=False,
			dir=path.parent,
			encoding="utf-8",
			prefix=f".{path.name}.",
			suffix=".tmp",
		) as temporary_file:
			temporary_path = Path(temporary_file.name)
			temporary_file.write(text)
			temporary_file.flush()
			os.fsync(temporary_file.fileno())

		os.replace(temporary_path, path)
		try:
			_fsync_parent_directory(path)
		except OSError as exc:
			LOGGER.warning(
				"JSON file %s was replaced, but syncing the parent directory failed: %s",
				path,
				exc,
			)
		temporary_path = None
	finally:
		if temporary_path is not None:
			temporary_path.unlink(missing_ok=True)


def _fsync_parent_directory(path: Path) -> None:
	if os.name != "posix":
		return

	directory_fd = os.open(path.parent, os.O_RDONLY)
	try:
		os.fsync(directory_fd)
	finally:
		os.close(directory_fd)


@contextmanager
def _json_file_lock(path: Path):
	lock_path = path.with_name(f"{path.name}.lock")
	lock_path.parent.mkdir(parents=True, exist_ok=True)

	with lock_path.open("a+b") as lock_file:
		_lock_file(lock_file)
		try:
			yield
		finally:
			_unlock_file(lock_file)


def _lock_file(lock_file: Any, timeout_seconds: float = FILE_LOCK_TIMEOUT_SECONDS) -> None:
	if os.name == "nt":
		import msvcrt

		lock_file.seek(0)
		deadline = time.monotonic() + timeout_seconds
		while True:
			try:
				msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
				return
			except OSError as exc:
				if time.monotonic() >= deadline:
					raise RuntimeError(
						f"Timed out waiting for file lock: {lock_file.name}"
					) from exc
				time.sleep(FILE_LOCK_RETRY_INTERVAL_SECONDS)
	else:
		import fcntl

		fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _unlock_file(lock_file: Any) -> None:
	if os.name == "nt":
		import msvcrt

		lock_file.seek(0)
		msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
	else:
		import fcntl

		fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _snapshot_file(path: Path) -> bytes | None:
	if not path.exists():
		return None
	return path.read_bytes()


def _restore_file(path: Path, snapshot: bytes | None) -> None:
	if snapshot is None:
		path.unlink(missing_ok=True)
		return

	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_bytes(snapshot)


def _file_size(path: Path) -> int | None:
	if not path.exists():
		return None
	return path.stat().st_size


def _truncate_file(path: Path, size: int | None) -> None:
	if size is None:
		path.unlink(missing_ok=True)
		return

	with path.open("r+b") as file:
		file.truncate(size)
		file.flush()
		os.fsync(file.fileno())


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
	with _json_file_lock(VOTES_FILE):
		_ensure_votes_file_unlocked()


def _ensure_votes_file_unlocked() -> None:
	# Appends create votes.jsonl on demand; this only migrates a legacy votes.json
	# list once. The legacy file is left in place.
	legacy_votes_file = VOTES_FILE.with_suffix(".json")
	if VOTES_FILE.exists() or not legacy_votes_file.exists():
		return

	legacy_records = _read_json_file(legacy_votes_file, list, [])
	_write_text_file(VOTES_FILE, "".join(f"{json.dumps(record)}\n" for record in legacy_records))


def _ensure_log_store() -> None:
	with _json_file_lock(META_LOG_FILE):
		_ensure_log_store_unlocked()


def _ensure_log_store_unlocked() -> None:
	LOGS_DIR.mkdir(parents=True, exist_ok=True)
	SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
	if not META_LOG_FILE.exists():
		_write_json_file(META_LOG_FILE, _empty_meta_log())


def _bootstrap_persistence() -> None:
	_ensure_votes_file()
	_ensure_log_store()


def _append_vote_record(record: dict[str, Any]) -> None:
	with _json_file_lock(VOTES_FILE):
		_append_vote_record_unlocked(record)


def _append_vote_record_unlocked(record: dict[str, Any]) -> None:
	_ensure_votes_file_unlocked()
	if not VOTES_FILE.exists():
		# Create through the atomic write path so the new directory entry is synced too.
		_write_text_file(VOTES_FILE, "")

	line = f"{json.dumps(record)}\n"
	if _ends_mid_line(VOTES_FILE):
		# A crash during an earlier append can leave a partial last line; start a
		# new line so this record is not glued onto it.
		line = f"\n{line}"

	with VOTES_FILE.open("a", encoding="utf-8") as votes_file:
		votes_file.write(line)
		votes_file.flush()
		os.fsync(votes_file.fileno())


def _ends_mid_line(path: Path) -> bool:
	with path.open("rb") as file:
		if file.seek(0, os.SEEK_END) == 0:
			return False
		file.seek(-1, os.SEEK_END)
		return file.read(1) != b"\n"


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
				"reasoning_payload": slot_log.get("reasoning_payload"),
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


def _update_meta_log(round_state: dict[str, Any]) -> None:
	with _json_file_lock(META_LOG_FILE):
		_update_meta_log_unlocked(round_state)


def _update_meta_log_unlocked(round_state: dict[str, Any]) -> None:
	_ensure_log_store_unlocked()
	meta_log = _read_json_file(META_LOG_FILE, dict, _empty_meta_log())
	# Schema 1 kept per-round summaries that duplicate the session logs; drop them.
	meta_log.pop("round_summaries", None)
	meta_log["schema_version"] = META_LOG_SCHEMA_VERSION
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

	_write_json_file(META_LOG_FILE, meta_log)


def _round_session_log_path(round_state: dict[str, Any]) -> tuple[Path, str]:
	round_id = str(round_state.get("round_id") or uuid4().hex)
	session_dir_name = f"{_timestamp_slug(str(round_state.get('submitted_at') or ''))}_{round_id}"
	session_dir = SESSION_LOGS_DIR / session_dir_name
	session_dir_relative = session_dir.relative_to(APP_DIR).as_posix()
	return session_dir, session_dir_relative


def _write_round_session_artifacts(round_state: dict[str, Any], session_dir: Path) -> None:
	round_id = str(round_state.get("round_id") or uuid4().hex)
	session_dir.mkdir(parents=True, exist_ok=True)

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
	except Exception:
		shutil.rmtree(session_dir, ignore_errors=True)
		raise


def _persist_vote_submission(round_state: dict[str, Any]) -> str:
	_ensure_log_store()
	session_dir, session_dir_relative = _round_session_log_path(round_state)

	with _json_file_lock(VOTES_FILE), _json_file_lock(META_LOG_FILE):
		# Votes are append-only, so rolling back means truncating to the old size.
		votes_size = _file_size(VOTES_FILE)
		meta_snapshot = _snapshot_file(META_LOG_FILE)

		try:
			_append_vote_record_unlocked(_build_vote_record(round_state, session_dir_relative))
			_write_round_session_artifacts(round_state, session_dir)
			_update_meta_log_unlocked(round_state)
		except Exception:
			shutil.rmtree(session_dir, ignore_errors=True)
			try:
				_restore_file(META_LOG_FILE, meta_snapshot)
			finally:
				_truncate_file(VOTES_FILE, votes_size)
			raise

	return session_dir_relative


def _reasoning_capabilities_for_model_id(model_id: str | None) -> dict[str, Any]:
	return reasoning_capabilities_for_model(CATALOG.lookup.get(model_id or "", {}))


def _reasoning_control_config(model_id: str | None) -> dict[str, dict[str, Any]]:
	capabilities = _reasoning_capabilities_for_model_id(model_id)
	control_type = capabilities.get("control_type")
	interactive = CATALOG.ready
	cost_hint = reasoning_cost_hint(capabilities)

	return {
		"effort": {
			"label": "Reasoning effort",
			"choices": capabilities.get("effort_choices") or [],
			"value": capabilities.get("default_effort"),
			"visible": control_type == "effort",
			"interactive": interactive,
		},
		"cost_hint": {
			"value": cost_hint,
			"visible": bool(cost_hint) and control_type != "none",
		},
	}


def _reasoning_control_updates(model_id: str | None) -> tuple[Any, Any]:
	config = _reasoning_control_config(model_id)
	return (
		gr.update(**config["effort"]),
		gr.update(**config["cost_hint"]),
	)


def _openrouter_status_banner() -> gr.HTML:
	if CATALOG.ready:
		return gr.HTML(value="", visible=False)

	message = (
		"OpenRouter is not ready. Fix OPENROUTER_API_KEY in your environment variables, "
		"then restart the app and retry."
	)

	return gr.HTML(
		value=(
			'<div style="'
			"margin-bottom: 1rem; padding: 1rem 1.25rem; border-radius: 14px; "
			"border: 2px solid #dc2626; background: #fee2e2; color: #7f1d1d; "
			"text-align: center; font-size: 1.05rem; font-weight: 700; line-height: 1.5;"
			f'">{escape(message)}</div>'
		),
		visible=True,
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
	resolved_model_id = CATALOG.resolve_model_for_provider(provider_key)
	return (
		gr.update(
			choices=CATALOG.model_choices_for_provider(provider_key),
			value=resolved_model_id,
			interactive=CATALOG.ready,
		),
		*_reasoning_control_updates(resolved_model_id),
		*_reset_arena_outputs(),
	)


def update_panel_model(model_id: str):
	return (
		*_reasoning_control_updates(model_id),
		*_reset_arena_outputs(),
	)


def _prepare_reasoning_request(model_id: str, effort: str | None) -> dict[str, Any]:
	model_entry = CATALOG.lookup.get(model_id, {})
	reasoning_payload = normalize_reasoning_payload(model_entry, {"effort": effort})
	reasoning_warning = None
	if effort not in (None, "none") and reasoning_payload is None:
		model_label = CATALOG.chatbot_label(model_id) if model_id else "This model"
		reasoning_warning = (
			f"Reasoning effort '{effort}' was selected, but {model_label} does not "
			"support an applied reasoning payload. The request will continue without "
			"reasoning controls."
		)
	return {
		"model_entry": model_entry,
		"reasoning_payload": reasoning_payload,
		"reasoning_warning": reasoning_warning,
	}


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
		"submitted_at": datetime.now(timezone.utc).isoformat(),
		"log_warning": None,
		"submission_status": None,
		"submission_message": None,
	}

	try:
		session_dir = _persist_vote_submission(candidate_state)
	except (RuntimeError, OSError) as exc:
		failed_state = {
			**current_state,
			"submitted": False,
			"log_warning": str(exc),
			"submission_status": "error",
			"submission_message": f"Vote could not be saved: {exc}",
		}
		return _submit_vote_outputs(failed_state)

	candidate_state["submitted"] = True
	candidate_state["vote_stage"] = "submitted"
	candidate_state["session_dir"] = session_dir
	candidate_state["submission_status"] = "success"
	candidate_state["submission_message"] = "Vote submitted and saved successfully."

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
	if chunk.get("event") == "error":
		errored_slots.add(slot)
		round_state["slot_logs"][slot]["status"] = "error"
		round_state["slot_logs"][slot]["error"] = str(chunk.get("error") or "Unknown error")
		histories[slot].append(
			_status_message(
				f"[Error] {round_state['slot_logs'][slot]['error']}",
				"Generation Error",
			)
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

	if chunk.get("event") in {"complete", "error"}:
		_finalize_round_state_logs(round_state, histories, reasoning_message_indices)
	return slot


def _finalize_generation_state(
	round_state: dict[str, Any],
	histories: list[list[Any]],
	assistant_message_indices: list[int | None],
	reasoning_message_indices: list[int | None],
	completed_slots: set[int],
	errored_slots: set[int],
) -> None:
	_finalize_round_state_logs(round_state, histories, reasoning_message_indices)
	round_state["completed_slots"] = sorted(completed_slots)
	round_state["errored_slots"] = sorted(errored_slots)
	round_state["generation_completed_at"] = datetime.now(timezone.utc).isoformat()
	round_state["ready_for_vote"] = len(completed_slots) + len(
		errored_slots
	) == PANEL_COUNT and bool(completed_slots)
	round_state["vote_stage"] = "pick_first" if completed_slots else "unavailable"


def _finalize_blocked_generation_state(
	round_state: dict[str, Any],
	histories: list[list[Any]],
	assistant_message_indices: list[int | None],
	reasoning_message_indices: list[int | None],
	blocked_slots: set[int],
) -> None:
	_finalize_generation_state(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots=set(),
		errored_slots=blocked_slots,
	)


async def stream_all_models(
	user_text: str,
	system_prompt: str,
	*panel_inputs: str | None,
):
	"""Run one round. `panel_inputs` holds each panel's model id, then each
	panel's reasoning effort; efforts are optional."""
	user_text = (user_text or "").strip()
	if not user_text:
		return

	model_ids = list(panel_inputs[:PANEL_COUNT])
	reasoning_efforts = list(panel_inputs[PANEL_COUNT:])
	reasoning_efforts += [None] * (PANEL_COUNT - len(reasoning_efforts))
	message_payload = _build_messages(user_text, system_prompt)
	display_order = _shuffled_display_order()
	reasoning_requests = [
		_prepare_reasoning_request(model_id, effort)
		for model_id, effort in zip(model_ids, reasoning_efforts)
	]
	round_state = _build_round_state(
		user_text,
		system_prompt,
		message_payload,
		model_ids,
		display_order,
		chatbot_label=CATALOG.chatbot_label,
		provider_for_model=CATALOG.provider_for_model,
	)
	for slot, request_metadata in enumerate(reasoning_requests):
		round_state["slot_logs"][slot]["reasoning_payload"] = request_metadata["reasoning_payload"]

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

	for slot, request_metadata in enumerate(reasoning_requests):
		reasoning_warning = request_metadata.get("reasoning_warning")
		if isinstance(reasoning_warning, str) and reasoning_warning:
			histories[slot].append(_status_message(f"[Warning] {reasoning_warning}", "Warning"))
	_finalize_round_state_logs(round_state, histories, reasoning_message_indices)

	yield _streaming_outputs(
		user_input="",
		chatbot_updates=_chatbot_updates(histories, display_order, round_state),
		round_state=round_state,
		vote_updates=_vote_ui_updates(round_state),
	)

	missing_selection = [index for index, model_id in enumerate(model_ids) if not model_id]
	if missing_selection:
		selected_but_unattempted = [
			index
			for index, model_id in enumerate(model_ids)
			if model_id and index not in missing_selection
		]
		for index in missing_selection:
			assistant_message_indices[index] = _upsert_assistant_message(
				history=histories[index],
				message_index=assistant_message_indices[index],
				content="No model selected for this panel.",
			)
			round_state["slot_logs"][index]["status"] = "blocked"
			round_state["slot_logs"][index]["error"] = "No model selected for this panel."
		for index in selected_but_unattempted:
			assistant_message_indices[index] = _upsert_assistant_message(
				history=histories[index],
				message_index=assistant_message_indices[index],
				content="Generation blocked because each panel needs a selected model.",
			)
			round_state["slot_logs"][index]["status"] = "blocked"
			round_state["slot_logs"][index]["error"] = (
				"Generation blocked because each panel needs a selected model."
			)
		_finalize_blocked_generation_state(
			round_state,
			histories,
			assistant_message_indices,
			reasoning_message_indices,
			set(range(PANEL_COUNT)),
		)
		yield _streaming_outputs(
			chatbot_updates=_targeted_chatbot_value_updates(
				histories, display_order, update_all=True
			),
			round_state=round_state,
			vote_updates=_vote_ui_updates(round_state),
		)
		return

	if not CATALOG.api_key:
		for index in range(PANEL_COUNT):
			assistant_message_indices[index] = _upsert_assistant_message(
				history=histories[index],
				message_index=assistant_message_indices[index],
				content="Missing OPENROUTER_API_KEY in environment.",
			)
			round_state["slot_logs"][index]["status"] = "blocked"
			round_state["slot_logs"][index]["error"] = "Missing OPENROUTER_API_KEY in environment."
		_finalize_blocked_generation_state(
			round_state,
			histories,
			assistant_message_indices,
			reasoning_message_indices,
			set(range(PANEL_COUNT)),
		)
		yield _streaming_outputs(
			chatbot_updates=_targeted_chatbot_value_updates(
				histories, display_order, update_all=True
			),
			round_state=round_state,
			vote_updates=_vote_ui_updates(round_state),
		)
		return

	api = OpenRouterAPI(api_key=CATALOG.api_key, site_url=SITE_URL, site_name=SITE_NAME)
	prompt_requests = [
		{
			"slot": slot,
			"model": model_id,
			"model_entry": reasoning_requests[slot]["model_entry"],
			"reasoning_payload": reasoning_requests[slot]["reasoning_payload"],
		}
		for slot, model_id in enumerate(model_ids)
	]

	async for chunk in api.prompt_models_concurrent(
		prompt_requests,
		message_payload,
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


@dataclass(frozen=True)
class ArenaDemo:
	"""The built Blocks plus the components callers inspect after building."""

	blocks: gr.Blocks
	panel_providers: list[gr.Dropdown]
	panel_models: list[gr.Dropdown]
	send_button: gr.Button
	status_banner: gr.HTML


def create_demo() -> ArenaDemo:
	interactive = CATALOG.ready
	initial_leaderboard_summary, initial_leaderboard_rows = _leaderboard_view_data()

	with gr.Blocks(title="LLM Council Arena") as blocks:
		gr.Markdown("# LLM Council Arena")

		with gr.Tabs():
			with gr.Tab("Arena"):
				openrouter_status_banner = _openrouter_status_banner()

				panel_providers: list[gr.Dropdown] = []
				panel_models: list[gr.Dropdown] = []
				panel_efforts: list[gr.Dropdown] = []
				panel_cost_hints: list[gr.Markdown] = []
				with gr.Row():
					for model_id in CATALOG.default_panel_model_ids:
						provider_key = CATALOG.provider_for_model(model_id)
						reasoning_config = _reasoning_control_config(model_id)
						with gr.Column(scale=1):
							panel_providers.append(
								gr.Dropdown(
									label="Provider",
									choices=CATALOG.provider_choices,
									value=provider_key,
									interactive=interactive,
								)
							)
							panel_models.append(
								gr.Dropdown(
									label="Model",
									choices=CATALOG.model_choices_for_provider(provider_key),
									value=model_id,
									interactive=interactive,
								)
							)
							panel_efforts.append(gr.Dropdown(**reasoning_config["effort"]))
							panel_cost_hints.append(gr.Markdown(**reasoning_config["cost_hint"]))

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
				send_btn = gr.Button("Send", interactive=interactive)
				round_state = gr.State(_empty_round_state())

				panel_chats: list[gr.Chatbot] = []
				with gr.Row():
					for index in range(PANEL_COUNT):
						with gr.Column(scale=1):
							panel_chats.append(
								_chatbot_config(label=_panel_label(index), height=520)
							)

				with gr.Group():
					gr.Markdown("## Vote on the Anonymous Responses")
					with gr.Row():
						vote_buttons = [
							gr.Button(_panel_label(index), interactive=False)
							for index in range(PANEL_COUNT)
						]
					with gr.Row():
						vote_reset_btn = gr.Button("Reset Vote", interactive=False)
						vote_submit_btn = gr.Button("Submit Vote", interactive=False)
					vote_status_banner = gr.HTML(value="", visible=False)

			with gr.Tab("Leaderboard"):
				leaderboard_summary_md = gr.Markdown(initial_leaderboard_summary)
				leaderboard_table = gr.Dataframe(
					headers=["Rank", "Model", "Provider", "Wins", "Appearances", "Win Rate"],
					value=initial_leaderboard_rows,
					interactive=False,
					wrap=True,
				)

		# Shared output groups; order matches _vote_ui_updates() and _reset_arena_outputs().
		vote_controls = [*vote_buttons, vote_reset_btn, vote_submit_btn, vote_status_banner]
		arena_reset_outputs = [*panel_chats, round_state, *vote_controls]

		for provider, model, effort, cost_hint in zip(
			panel_providers, panel_models, panel_efforts, panel_cost_hints
		):
			provider.change(
				fn=update_panel_provider,
				inputs=[provider],
				outputs=[model, effort, cost_hint, *arena_reset_outputs],
			)
			model.change(
				fn=update_panel_model,
				inputs=[model],
				outputs=[effort, cost_hint, *arena_reset_outputs],
			)

		gr.on(
			triggers=[user_input.submit, send_btn.click],
			fn=stream_all_models,
			inputs=[user_input, system_prompt, *panel_models, *panel_efforts],
			outputs=[user_input, *panel_chats, round_state, *vote_controls],
			show_progress="hidden",
		)
		for index, vote_button in enumerate(vote_buttons):
			vote_button.click(
				fn=partial(vote_response, display_index=index),
				inputs=[round_state],
				outputs=[round_state, *vote_controls],
			)
		vote_reset_btn.click(
			fn=reset_vote,
			inputs=[round_state],
			outputs=[round_state, *vote_controls],
		)
		vote_submit_btn.click(
			fn=submit_vote,
			inputs=[round_state],
			outputs=[
				round_state,
				*panel_chats,
				*vote_controls,
				leaderboard_summary_md,
				leaderboard_table,
			],
		)
		blocks.load(
			fn=_leaderboard_view_data,
			inputs=None,
			outputs=[leaderboard_summary_md, leaderboard_table],
		)

	return ArenaDemo(
		blocks=blocks,
		panel_providers=panel_providers,
		panel_models=panel_models,
		send_button=send_btn,
		status_banner=openrouter_status_banner,
	)
