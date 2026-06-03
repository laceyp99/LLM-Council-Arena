from typing import Any

import gradio as gr

from arena.state.round import (
	_display_order_from_state,
	_empty_round_state,
	_panel_label,
	_slot_logs_from_state,
)
from arena.ui.config import ANONYMOUS_PANEL_LABELS


def _choice_to_display_index(choice: str) -> int | None:
	try:
		return ANONYMOUS_PANEL_LABELS.index(choice)
	except ValueError:
		return None


def _ranking_choices_from_state(
	round_state: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
	if not isinstance(round_state, dict):
		return None, None, None

	selections: list[str | None] = []
	for key in ("first_choice", "second_choice", "third_choice"):
		choice = round_state.get(key)
		selections.append(choice if choice in ANONYMOUS_PANEL_LABELS else None)
	return tuple(selections)


def _remaining_vote_choices(round_state: dict[str, Any] | None) -> list[str]:
	first_choice, second_choice, _ = _ranking_choices_from_state(round_state)
	blocked = {choice for choice in (first_choice, second_choice) if choice}
	return [label for label in ANONYMOUS_PANEL_LABELS if label not in blocked]


def _display_mapping_from_state(round_state: dict[str, Any]) -> list[dict[str, Any]]:
	display_order = _display_order_from_state(round_state)
	slot_logs = _slot_logs_from_state(round_state)
	mapping: list[dict[str, Any]] = []

	for display_index, slot in enumerate(display_order):
		if slot >= len(slot_logs):
			continue
		slot_log = slot_logs[slot]
		mapping.append(
			{
				"response_label": _panel_label(display_index),
				"selection_slot": slot,
				"model_id": slot_log.get("model_id") or "",
				"model_label": slot_log.get("model_label") or "",
				"provider_key": slot_log.get("provider_key") or "",
			}
		)

	return mapping


def _ranking_details_from_state(round_state: dict[str, Any]) -> list[dict[str, Any]]:
	display_order = _display_order_from_state(round_state)
	slot_logs = _slot_logs_from_state(round_state)
	ranking_details: list[dict[str, Any]] = []

	for rank_index, choice in enumerate(_ranking_choices_from_state(round_state), start=1):
		if not choice:
			continue
		display_index = _choice_to_display_index(choice)
		if display_index is None or display_index >= len(display_order):
			continue
		slot = display_order[display_index]
		if slot >= len(slot_logs):
			continue
		slot_log = slot_logs[slot]
		ranking_details.append(
			{
				"rank": rank_index,
				"response_label": choice,
				"selection_slot": slot,
				"model_id": slot_log.get("model_id") or "",
				"model_label": slot_log.get("model_label") or "",
				"provider_key": slot_log.get("provider_key") or "",
			}
		)

	return ranking_details


def _vote_button_text(label: str, round_state: dict[str, Any] | None) -> str:
	first_choice, second_choice, third_choice = _ranking_choices_from_state(round_state)
	if label == first_choice:
		return f"{label} - 1st"
	if label == second_choice:
		return f"{label} - 2nd"
	if label == third_choice:
		return f"{label} - 3rd"
	return label


def _vote_controls_updates(
	round_state: dict[str, Any] | None,
) -> tuple[gr.Button, gr.Button, gr.Button, gr.Button, gr.Button]:
	current_state = round_state if isinstance(round_state, dict) else _empty_round_state()
	first_choice, second_choice, third_choice = _ranking_choices_from_state(current_state)
	remaining_choices = set(_remaining_vote_choices(current_state))
	ready_for_vote = bool(current_state.get("ready_for_vote"))
	submitted = bool(current_state.get("submitted"))

	button_updates = []
	for label in ANONYMOUS_PANEL_LABELS:
		interactive = False
		if ready_for_vote and not submitted:
			if first_choice is None:
				interactive = True
			elif second_choice is None:
				interactive = label in remaining_choices
		button_updates.append(
			gr.Button(value=_vote_button_text(label, current_state), interactive=interactive)
		)

	reset_interactive = (
		ready_for_vote and not submitted and any((first_choice, second_choice, third_choice))
	)
	submit_interactive = (
		ready_for_vote and not submitted and all((first_choice, second_choice, third_choice))
	)
	return (
		*button_updates,
		gr.Button("Reset Vote", interactive=reset_interactive),
		gr.Button("Submit Vote", interactive=submit_interactive),
	)


def _submission_status_update(round_state: dict[str, Any] | None) -> gr.Markdown:
	current_state = round_state if isinstance(round_state, dict) else _empty_round_state()
	message = str(current_state.get("submission_message") or "").strip()
	return gr.Markdown(value=message, visible=bool(message))


def _vote_ui_updates(
	round_state: dict[str, Any] | None,
) -> Any:
	current_state = round_state if isinstance(round_state, dict) else _empty_round_state()
	return (*_vote_controls_updates(current_state), _submission_status_update(current_state))


def _vote_response(round_state: dict[str, Any] | None, response_label: str):
	current_state = round_state if isinstance(round_state, dict) else _empty_round_state()

	if not current_state.get("round_id"):
		return current_state
	if current_state.get("submitted"):
		return current_state
	if not current_state.get("ready_for_vote"):
		return current_state
	if response_label not in ANONYMOUS_PANEL_LABELS:
		return current_state

	first_choice, second_choice, _ = _ranking_choices_from_state(current_state)
	if first_choice is None:
		updated_state = {
			**current_state,
			"first_choice": response_label,
			"second_choice": None,
			"third_choice": None,
			"vote_stage": "pick_second",
		}
		return updated_state

	if second_choice is None:
		if response_label == first_choice:
			return current_state
		third_choice = next(
			(
				label
				for label in ANONYMOUS_PANEL_LABELS
				if label not in {first_choice, response_label}
			),
			None,
		)
		updated_state = {
			**current_state,
			"second_choice": response_label,
			"third_choice": third_choice,
			"vote_stage": "ready_submit",
		}
		return updated_state

	return current_state


def vote_response_a(round_state: dict[str, Any] | None):
	updated_state = _vote_response(round_state, _panel_label(0))
	return updated_state, *_vote_ui_updates(updated_state)


def vote_response_b(round_state: dict[str, Any] | None):
	updated_state = _vote_response(round_state, _panel_label(1))
	return updated_state, *_vote_ui_updates(updated_state)


def vote_response_c(round_state: dict[str, Any] | None):
	updated_state = _vote_response(round_state, _panel_label(2))
	return updated_state, *_vote_ui_updates(updated_state)


def reset_vote(round_state: dict[str, Any] | None):
	current_state = round_state if isinstance(round_state, dict) else _empty_round_state()
	if not current_state.get("round_id"):
		return current_state, *_vote_ui_updates(current_state)
	if current_state.get("submitted"):
		return current_state, *_vote_ui_updates(current_state)
	if not current_state.get("ready_for_vote"):
		return current_state, *_vote_ui_updates(current_state)

	updated_state = {
		**current_state,
		"first_choice": None,
		"second_choice": None,
		"third_choice": None,
		"vote_stage": "pick_first",
	}
	return updated_state, *_vote_ui_updates(updated_state)
