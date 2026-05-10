import hashlib

from arena.state.round import (
	_build_round_state,
	_default_display_order,
	_display_order_from_state,
)
from arena.ui.config import DEFAULT_SYSTEM_PROMPT, PANEL_COUNT


def test_default_display_order_matches_panel_count() -> None:
	assert _default_display_order() == [0, 1, 2]
	assert len(_default_display_order()) == PANEL_COUNT


def test_display_order_from_state_falls_back_on_invalid_state() -> None:
	assert _display_order_from_state(None) == _default_display_order()
	assert _display_order_from_state({"display_order": [0, 1]}) == _default_display_order()
	assert _display_order_from_state({"display_order": [0, 0, 2]}) == _default_display_order()
	assert _display_order_from_state({"display_order": ["a", 1, 2]}) == _default_display_order()


def test_build_round_state_sets_defaults_and_slot_logs() -> None:
	message_payload = [{"role": "user", "content": "Compare these models."}]
	round_state = _build_round_state(
		user_text="Compare these models.",
		system_prompt="   ",
		message_payload=message_payload,
		model_ids=["model-a", "model-b", "model-c"],
		display_order=[2, 0, 1],
		chatbot_label=lambda model_id: f"Label for {model_id}",
		provider_for_model=lambda model_id: f"provider:{model_id}",
	)

	assert round_state["system_prompt"] == DEFAULT_SYSTEM_PROMPT
	assert (
		round_state["prompt_sha256"]
		== hashlib.sha256("Compare these models.".encode("utf-8")).hexdigest()
	)
	assert round_state["vote_stage"] == "streaming"
	assert round_state["ready_for_vote"] is False
	assert [slot_log["response_label"] for slot_log in round_state["slot_logs"]] == [
		"Response B",
		"Response C",
		"Response A",
	]
