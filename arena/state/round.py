import hashlib
import random
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from arena.ui.config import ANONYMOUS_PANEL_LABELS, DEFAULT_SYSTEM_PROMPT, PANEL_COUNT


def _default_display_order() -> list[int]:
	return list(range(PANEL_COUNT))


def _shuffled_display_order() -> list[int]:
	return random.sample(range(PANEL_COUNT), k=PANEL_COUNT)


def _panel_label(display_index: int) -> str:
	return ANONYMOUS_PANEL_LABELS[display_index]


def _display_label_for_slot(slot: int, display_order: list[int]) -> str:
	for display_index, mapped_slot in enumerate(display_order):
		if mapped_slot == slot:
			return _panel_label(display_index)
	return _panel_label(slot)


def _empty_slot_log(
	slot: int,
	model_id: str,
	display_order: list[int],
	chatbot_label: Callable[[str], str],
	provider_for_model: Callable[[str], str],
) -> dict[str, Any]:
	return {
		"selection_slot": slot,
		"response_label": _display_label_for_slot(slot, display_order),
		"model_id": model_id,
		"model_label": chatbot_label(model_id) if model_id else "",
		"provider_key": provider_for_model(model_id) if model_id else "",
		"status": "pending",
		"error": None,
		"final_response": "",
		"reasoning_trace": "",
		"reasoning_details": [],
		"usage": {},
		"stats": {},
		"finish_reason": None,
		"cost": None,
		"completion_tokens": None,
		"reasoning_tokens": None,
		"message_history": [],
	}


def _build_slot_logs(
	model_ids: list[str],
	display_order: list[int],
	chatbot_label: Callable[[str], str],
	provider_for_model: Callable[[str], str],
) -> list[dict[str, Any]]:
	return [
		_empty_slot_log(
			slot=index,
			model_id=model_ids[index] if index < len(model_ids) else "",
			display_order=display_order,
			chatbot_label=chatbot_label,
			provider_for_model=provider_for_model,
		)
		for index in range(PANEL_COUNT)
	]


def _empty_round_state() -> dict[str, Any]:
	return {
		"round_id": None,
		"created_at": None,
		"generation_completed_at": None,
		"submitted_at": None,
		"user_text": "",
		"prompt_sha256": "",
		"system_prompt": DEFAULT_SYSTEM_PROMPT,
		"messages_payload": [],
		"model_ids": [],
		"display_order": _default_display_order(),
		"slot_logs": [],
		"completed_slots": [],
		"errored_slots": [],
		"ready_for_vote": False,
		"submitted": False,
		"vote_stage": "idle",
		"first_choice": None,
		"second_choice": None,
		"third_choice": None,
		"session_dir": None,
		"log_warning": None,
		"submission_status": None,
		"submission_message": None,
	}


def _build_round_state(
	user_text: str,
	system_prompt: str,
	message_payload: list[dict[str, str]],
	model_ids: list[str],
	display_order: list[int],
	chatbot_label: Callable[[str], str],
	provider_for_model: Callable[[str], str],
) -> dict[str, Any]:
	resolved_system_prompt = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
	return {
		"round_id": uuid4().hex,
		"created_at": datetime.now(timezone.utc).isoformat(),
		"generation_completed_at": None,
		"submitted_at": None,
		"user_text": user_text,
		"prompt_sha256": hashlib.sha256(user_text.encode("utf-8")).hexdigest(),
		"system_prompt": resolved_system_prompt,
		"messages_payload": [dict(message) for message in message_payload],
		"model_ids": list(model_ids),
		"display_order": list(display_order),
		"slot_logs": _build_slot_logs(model_ids, display_order, chatbot_label, provider_for_model),
		"completed_slots": [],
		"errored_slots": [],
		"ready_for_vote": False,
		"submitted": False,
		"vote_stage": "streaming",
		"first_choice": None,
		"second_choice": None,
		"third_choice": None,
		"session_dir": None,
		"log_warning": None,
		"submission_status": None,
		"submission_message": None,
	}


def _display_order_from_state(round_state: dict[str, Any] | None) -> list[int]:
	if not isinstance(round_state, dict):
		return _default_display_order()

	display_order = round_state.get("display_order")
	if not isinstance(display_order, list) or len(display_order) != PANEL_COUNT:
		return _default_display_order()

	try:
		normalized_order = [int(slot) for slot in display_order]
	except (TypeError, ValueError):
		return _default_display_order()

	if sorted(normalized_order) != _default_display_order():
		return _default_display_order()

	return normalized_order


def _model_ids_from_state(round_state: dict[str, Any] | None) -> list[str]:
	if not isinstance(round_state, dict):
		return []

	model_ids = round_state.get("model_ids")
	if not isinstance(model_ids, list):
		return []

	return [str(model_id) for model_id in model_ids[:PANEL_COUNT]]


def _slot_logs_from_state(round_state: dict[str, Any] | None) -> list[dict[str, Any]]:
	if not isinstance(round_state, dict):
		return []

	slot_logs = round_state.get("slot_logs")
	if not isinstance(slot_logs, list):
		return []

	return [dict(slot_log) for slot_log in slot_logs[:PANEL_COUNT]]
