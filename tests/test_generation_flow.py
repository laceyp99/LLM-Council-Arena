import gradio as gr
import pytest

from arena import app as app_module


def _build_generation_context() -> tuple[
	dict[str, object],
	list[list[object]],
	list[int | None],
	list[int | None],
	set[int],
	set[int],
]:
	user_text = "Compare the models."
	round_state = app_module._build_round_state(
		user_text=user_text,
		system_prompt="System prompt",
		message_payload=app_module._build_messages(user_text, "System prompt"),
		model_ids=["alpha/one", "beta/two", "gamma/three"],
		display_order=[0, 1, 2],
		chatbot_label=lambda model_id: f"Label for {model_id}",
		provider_for_model=lambda model_id: model_id.split("/", 1)[0],
	)
	histories: list[list[object]] = [
		[{"role": "user", "content": user_text}] for _ in range(app_module.PANEL_COUNT)
	]
	assistant_message_indices: list[int | None] = [None for _ in range(app_module.PANEL_COUNT)]
	reasoning_message_indices: list[int | None] = [None for _ in range(app_module.PANEL_COUNT)]
	completed_slots: set[int] = set()
	errored_slots: set[int] = set()
	return (
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	)


def test_apply_stream_chunk_appends_delta_and_updates_slot_log() -> None:
	(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	) = _build_generation_context()

	slot = app_module._apply_stream_chunk(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
		{"slot": 0, "delta": "Hello world"},
	)

	assert slot == 0
	assert round_state["slot_logs"][0]["status"] == "streaming"
	assert round_state["slot_logs"][0]["final_response"] == "Hello world"
	assert histories[0][1] == {"role": "assistant", "content": "Hello world"}
	assert round_state["slot_logs"][0]["message_history"][1]["content"] == "Hello world"


def test_apply_stream_chunk_finalizes_reasoning_on_error() -> None:
	(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	) = _build_generation_context()

	app_module._apply_stream_chunk(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
		{
			"slot": 1,
			"event": "reasoning",
			"reasoning_details": [{"type": "reasoning.summary", "summary": "Short summary"}],
			"usage": {"completion_tokens_details": {"reasoning_tokens": 3}},
		},
	)
	slot = app_module._apply_stream_chunk(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
		{"slot": 1, "event": "error", "error": "provider failure"},
	)

	assert slot == 1
	assert 1 in errored_slots
	assert round_state["slot_logs"][1]["status"] == "error"
	assert "provider failure" in str(round_state["slot_logs"][1]["error"])
	assert isinstance(histories[1][1], gr.ChatMessage)
	assert histories[1][1].metadata["status"] == "done"
	assert histories[1][2]["content"] == "[Error] provider failure"


def test_apply_stream_chunk_complete_records_usage_and_stats_footer() -> None:
	(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	) = _build_generation_context()

	app_module._apply_stream_chunk(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
		{"slot": 2, "delta": "Final answer"},
	)
	slot = app_module._apply_stream_chunk(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
		{
			"slot": 2,
			"event": "complete",
			"usage": {"cost": 0.002, "completion_tokens": 42},
			"stats": {"finish_reason": "stop", "total_generation_time": 2.4},
			"reasoning_details": [{"type": "reasoning.summary", "summary": "Summary"}],
		},
	)

	assert slot == 2
	assert 2 in completed_slots
	assert round_state["slot_logs"][2]["status"] == "complete"
	assert round_state["slot_logs"][2]["completion_tokens"] == 42
	assert round_state["slot_logs"][2]["cost"] == 0.002
	assert round_state["slot_logs"][2]["finish_reason"] == "stop"
	assert isinstance(histories[2][-1], gr.ChatMessage)
	assert histories[2][-1].metadata["title"] == "🛠️ Generation Stats"


def test_finalize_generation_state_unlocks_vote_when_all_panels_finish() -> None:
	(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	) = _build_generation_context()

	completed_slots.update({0, 2})
	errored_slots.add(1)
	app_module._finalize_generation_state(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	)

	assert round_state["completed_slots"] == [0, 2]
	assert round_state["errored_slots"] == [1]
	assert round_state["ready_for_vote"] is True
	assert round_state["vote_stage"] == "pick_first"
	assert round_state["generation_completed_at"] is not None


def test_finalize_generation_state_marks_vote_unavailable_without_completions() -> None:
	(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	) = _build_generation_context()

	errored_slots.update({0, 1, 2})
	app_module._finalize_generation_state(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	)

	assert round_state["ready_for_vote"] is False
	assert round_state["vote_stage"] == "unavailable"


@pytest.mark.anyio
async def test_stream_all_models_blocks_when_panel_has_no_model_selected(monkeypatch) -> None:
	monkeypatch.setattr(
		app_module,
		"_chatbot_updates",
		lambda *args, **kwargs: ("panel-1", "panel-2", "panel-3"),
	)
	monkeypatch.setattr(
		app_module,
		"_targeted_chatbot_value_updates",
		lambda *args, **kwargs: ("panel-1", "panel-2", "panel-3"),
	)
	monkeypatch.setattr(
		app_module,
		"_vote_ui_updates",
		lambda *args, **kwargs: (
			"vote-a",
			"vote-b",
			"vote-c",
			"vote-reset",
			"vote-submit",
		),
	)

	outputs = [
		output
		async for output in app_module.stream_all_models(
			"Compare models.",
			"System prompt",
			"alpha/one",
			"",
			"gamma/three",
		)
	]

	final_round_state = outputs[-1][4]

	assert len(outputs) == 2
	assert final_round_state["slot_logs"][1]["status"] == "blocked"
	assert final_round_state["slot_logs"][1]["error"] == "No model selected for this panel."
