import gradio as gr

from arena.ui.display import (
	_chatbot_config,
	_chatbot_panel_label,
	_extract_reasoning_tokens,
	_finalize_round_state_logs,
	_format_reasoning_details,
	_message_text_content,
	_reasoning_trace_title,
	_serialize_history,
	_stats_footer,
	_upsert_assistant_message,
	_upsert_reasoning_message,
)


def test_chatbot_config_uses_messages_type() -> None:
	chatbot = _chatbot_config(label="Panel A")

	assert chatbot.type == "messages"


def test_chatbot_panel_label_uses_model_label_after_submission() -> None:
	round_state = {
		"submitted": True,
		"display_order": [2, 0, 1],
		"slot_logs": [
			{"model_label": "Alpha"},
			{"model_label": "Beta"},
			{"model_label": "Gamma"},
		],
	}

	assert _chatbot_panel_label(0, round_state) == "Gamma"
	assert _chatbot_panel_label(1, round_state) == "Alpha"


def test_message_text_content_and_serialize_history_handle_supported_types() -> None:
	history = [
		gr.ChatMessage(role="assistant", content="Hello", metadata={"title": "Greeting"}),
		{"role": "user", "content": "Hi", "metadata": {"source": "test"}},
		42,
	]

	serialized = _serialize_history(history)

	assert _message_text_content(history[0]) == "Hello"
	assert _message_text_content(history[1]) == "Hi"
	assert serialized == [
		{"role": "assistant", "content": "Hello", "metadata": {"title": "Greeting"}},
		{"role": "user", "content": "Hi", "metadata": {"source": "test"}},
		{"role": "assistant", "content": "42"},
	]


def test_finalize_round_state_logs_captures_histories_and_outputs() -> None:
	round_state = {
		"slot_logs": [
			{"selection_slot": 0, "final_response": "Accumulated answer"},
			{"selection_slot": 1},
			{"selection_slot": 2, "final_response": "Another accumulated answer"},
		]
	}
	histories = [
		[
			{"role": "assistant", "content": "Final answer"},
			{"role": "assistant", "content": "Reasoning trail"},
		],
		[],
		[{"role": "assistant", "content": "Another answer"}],
	]

	_finalize_round_state_logs(round_state, histories, [1, None, None])

	assert round_state["slot_logs"][0]["final_response"] == "Accumulated answer"
	assert round_state["slot_logs"][0]["reasoning_trace"] == "Reasoning trail"
	assert round_state["slot_logs"][2]["final_response"] == "Another accumulated answer"
	assert round_state["slot_logs"][1]["message_history"] == []


def test_reasoning_helpers_render_summary_redaction_and_stats() -> None:
	reasoning_details = [
		{"type": "reasoning.text", "text": "Raw explanation"},
		{"type": "reasoning.summary", "summary": "Short summary"},
		{"type": "reasoning.encrypted", "data": "[REDACTED] payload"},
		{"type": "reasoning.unknown", "data": "?"},
	]

	formatted = _format_reasoning_details(reasoning_details)
	history = _stats_footer(
		[],
		{
			"stats": {
				"time_to_first_token": 0.5,
				"total_generation_time": 2.0,
				"tokens_per_second": 8.0,
				"finish_reason": "stop",
			},
			"usage": {
				"cost": 0.0123,
				"completion_tokens": 25,
				"completion_tokens_details": {"reasoning_tokens": 3},
			},
		},
	)

	assert "**Summary**" in formatted
	assert "Short summary" in formatted
	assert "redacted by the provider" in formatted
	assert "unsupported reasoning block" in formatted
	assert _extract_reasoning_tokens({"completion_tokens_details": {"reasoning_tokens": "4"}}) == 4
	assert _reasoning_trace_title({"completion_tokens_details": {"reasoning_tokens": 1}}).endswith(
		"1 token"
	)
	assert _reasoning_trace_title(unavailable=True) == "🔎 Reasoning Trace (not exposed)"
	assert "Completion tokens: 25" in history[-1].content
	assert "API cost: $0.0123" in history[-1].content


def test_upsert_message_helpers_append_and_insert_consistently() -> None:
	history: list[object] = []
	assistant_index = _upsert_assistant_message(history, None, "First answer")
	reasoning_index, assistant_index = _upsert_reasoning_message(
		history,
		message_index=None,
		slot=1,
		content="Reasoning",
		assistant_message_index=assistant_index,
	)
	assistant_index = _upsert_assistant_message(history, assistant_index, " updated", append=True)

	assert reasoning_index == 0
	assert assistant_index == 1
	assert history[1]["content"] == "First answer updated"
	assert isinstance(history[0], gr.ChatMessage)
	assert history[0].metadata["id"] == "reasoning-1"
