import json

import gradio as gr
import pytest

from arena import app as app_module
from arena.core import api as api_module


def _stub_ui_helpers(monkeypatch) -> None:
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


async def _collect_stream_outputs(*args: object) -> list[tuple[object, ...]]:
	if len(args) == 5:
		args = (
			*args,
			"medium",
			"medium",
			"medium",
		)
	return [output async for output in app_module.stream_all_models(*args)]


def _fake_streaming_api(monkeypatch, chunks: list[dict[str, object]]) -> list[dict[str, object]]:
	requests: list[dict[str, object]] = []

	class FakeOpenRouterAPI:
		def __init__(self, api_key: str, site_url: str, site_name: str) -> None:
			requests.append(
				{
					"api_key": api_key,
					"site_url": site_url,
					"site_name": site_name,
				}
			)

		async def prompt_models_concurrent(self, prompt_requests, message_payload, **kwargs):
			requests.append(
				{
					"prompt_requests": prompt_requests,
					"message_payload": message_payload,
					"kwargs": kwargs,
				}
			)
			for chunk in chunks:
				yield dict(chunk)

	monkeypatch.setattr(app_module, "OpenRouterAPI", FakeOpenRouterAPI)
	return requests


class _NoopAsyncClient:
	def __init__(self, *args, **kwargs) -> None:
		self.args = args
		self.kwargs = kwargs

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc, tb) -> None:
		return None


class _CapturedStreamResponse:
	def __init__(self, model: str) -> None:
		self.model = model

	def raise_for_status(self) -> None:
		return None

	async def aiter_lines(self):
		yield "data: " + json.dumps(
			{
				"choices": [
					{
						"delta": {"content": f"{self.model} answer"},
						"finish_reason": None,
					}
				]
			}
		)
		yield "data: " + json.dumps(
			{
				"choices": [{"delta": {}, "finish_reason": "stop"}],
				"usage": {"completion_tokens": 3},
			}
		)
		yield "data: [DONE]"


class _CapturedStreamContext:
	def __init__(self, response: _CapturedStreamResponse) -> None:
		self.response = response

	async def __aenter__(self) -> _CapturedStreamResponse:
		return self.response

	async def __aexit__(self, exc_type, exc, tb) -> None:
		return None


async def _failing_prompt_model(self, client, request, messages, **kwargs):
	yield {
		"slot": request["slot"],
		"model": request["model"],
		"delta": "first chunk",
	}
	raise RuntimeError("producer exploded")


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


def test_apply_stream_chunk_appends_delta_without_finalizing_logs(monkeypatch) -> None:
	(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
	) = _build_generation_context()
	finalize_calls: list[object] = []
	original_finalize = app_module._finalize_round_state_logs

	def track_finalization(*args) -> None:
		finalize_calls.append(args)
		original_finalize(*args)

	monkeypatch.setattr(app_module, "_finalize_round_state_logs", track_finalization)

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
	assert finalize_calls == []
	assert round_state["slot_logs"][0]["message_history"] == []


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
	app_module._apply_stream_chunk(
		round_state,
		histories,
		assistant_message_indices,
		reasoning_message_indices,
		completed_slots,
		errored_slots,
		{"slot": 1, "delta": "Partial answer"},
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
	assert round_state["slot_logs"][1]["final_response"] == "Partial answer"
	assert isinstance(histories[1][1], gr.ChatMessage)
	assert histories[1][1].metadata["status"] == "done"
	assert histories[1][2]["content"] == "Partial answer"
	assert isinstance(histories[1][3], gr.ChatMessage)
	assert histories[1][3].content == "[Error] provider failure"
	assert histories[1][3].metadata["title"] == "Generation Error"


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
	assert round_state["slot_logs"][2]["message_history"][-1]["metadata"]["title"] == (
		"🛠️ Generation Stats"
	)
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
async def test_stream_all_models_starts_new_round_with_cleared_submission_status(
	monkeypatch,
) -> None:
	_stub_ui_helpers(monkeypatch)
	monkeypatch.setattr(app_module, "OPENROUTER_API_KEY", None)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"beta/two",
		"gamma/three",
	)

	initial_round_state = outputs[0][4]

	assert initial_round_state["submission_status"] is None
	assert initial_round_state["submission_message"] is None


@pytest.mark.anyio
async def test_stream_all_models_blocks_when_panel_has_no_model_selected(monkeypatch) -> None:
	_stub_ui_helpers(monkeypatch)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"",
		"gamma/three",
	)

	final_round_state = outputs[-1][4]

	assert len(outputs) == 2
	assert final_round_state["generation_completed_at"] is not None
	assert final_round_state["completed_slots"] == []
	assert final_round_state["errored_slots"] == [0, 1, 2]
	assert final_round_state["ready_for_vote"] is False
	assert final_round_state["vote_stage"] == "unavailable"
	assert [slot_log["status"] for slot_log in final_round_state["slot_logs"]] == [
		"blocked",
		"blocked",
		"blocked",
	]
	assert (
		final_round_state["slot_logs"][0]["error"]
		== "Generation blocked because each panel needs a selected model."
	)
	assert final_round_state["slot_logs"][1]["status"] == "blocked"
	assert final_round_state["slot_logs"][1]["error"] == "No model selected for this panel."
	assert (
		final_round_state["slot_logs"][2]["error"]
		== "Generation blocked because each panel needs a selected model."
	)


@pytest.mark.anyio
async def test_stream_all_models_blocks_when_api_key_is_missing(monkeypatch) -> None:
	_stub_ui_helpers(monkeypatch)
	monkeypatch.setattr(app_module, "OPENROUTER_API_KEY", None)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"beta/two",
		"gamma/three",
	)

	final_round_state = outputs[-1][4]

	assert len(outputs) == 2
	assert final_round_state["generation_completed_at"] is not None
	assert final_round_state["completed_slots"] == []
	assert final_round_state["errored_slots"] == [0, 1, 2]
	assert final_round_state["ready_for_vote"] is False
	assert final_round_state["vote_stage"] == "unavailable"
	assert [slot_log["status"] for slot_log in final_round_state["slot_logs"]] == [
		"blocked",
		"blocked",
		"blocked",
	]
	assert all(
		slot_log["error"] == "Missing OPENROUTER_API_KEY in environment."
		for slot_log in final_round_state["slot_logs"]
	)


@pytest.mark.anyio
async def test_stream_all_models_completes_successfully_with_fake_stream(monkeypatch) -> None:
	_stub_ui_helpers(monkeypatch)
	monkeypatch.setattr(app_module, "OPENROUTER_API_KEY", "test-api-key")
	monkeypatch.setattr(app_module, "_shuffled_display_order", lambda: [0, 1, 2])
	monkeypatch.setattr(
		app_module,
		"MODEL_LOOKUP",
		{
			"beta/two": {
				"full_label": "Beta Two",
				"provider_key": "beta",
				"supported_parameters": ["reasoning"],
			},
			"gamma/three": {
				"full_label": "Gamma Three",
				"provider_key": "gamma",
				"supported_parameters": ["reasoning"],
			},
		},
	)
	requests = _fake_streaming_api(
		monkeypatch,
		[
			{"slot": 0, "delta": "Alpha answer"},
			{
				"slot": 1,
				"event": "reasoning",
				"reasoning_details": [{"type": "reasoning.summary", "summary": "Beta summary"}],
				"usage": {"completion_tokens_details": {"reasoning_tokens": 5}},
			},
			{
				"slot": 0,
				"event": "complete",
				"usage": {"completion_tokens": 20, "cost": 0.001},
				"stats": {"finish_reason": "stop", "total_generation_time": 1.2},
			},
			{"slot": 1, "delta": "Beta answer"},
			{
				"slot": 1,
				"event": "complete",
				"usage": {
					"completion_tokens": 25,
					"cost": 0.002,
					"completion_tokens_details": {"reasoning_tokens": 5},
				},
				"reasoning_details": [{"type": "reasoning.summary", "summary": "Beta summary"}],
				"stats": {"finish_reason": "stop", "total_generation_time": 1.5},
			},
			{"slot": 2, "delta": "Gamma answer"},
			{
				"slot": 2,
				"event": "complete",
				"usage": {"completion_tokens": 30, "cost": 0.003},
				"stats": {"finish_reason": "stop", "total_generation_time": 1.8},
			},
		],
	)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"beta/two",
		"gamma/three",
		None,
		"none",
		"high",
	)

	final_round_state = outputs[-1][4]

	assert requests[0]["api_key"] == "test-api-key"
	assert requests[1]["prompt_requests"] == [
		{
			"slot": 0,
			"model": "alpha/one",
			"model_entry": {},
			"reasoning_payload": None,
		},
		{
			"slot": 1,
			"model": "beta/two",
			"model_entry": {
				"full_label": "Beta Two",
				"provider_key": "beta",
				"supported_parameters": ["reasoning"],
			},
			"reasoning_payload": {"effort": "none"},
		},
		{
			"slot": 2,
			"model": "gamma/three",
			"model_entry": {
				"full_label": "Gamma Three",
				"provider_key": "gamma",
				"supported_parameters": ["reasoning"],
			},
			"reasoning_payload": {"effort": "high", "exclude": False},
		},
	]
	assert requests[1]["kwargs"] == {}
	assert final_round_state["ready_for_vote"] is True
	assert final_round_state["vote_stage"] == "pick_first"
	assert final_round_state["completed_slots"] == [0, 1, 2]
	assert final_round_state["errored_slots"] == []
	assert final_round_state["slot_logs"][0]["final_response"] == "Alpha answer"
	assert final_round_state["slot_logs"][0]["reasoning_payload"] is None
	assert final_round_state["slot_logs"][1]["reasoning_trace"] == "**Summary**\n\nBeta summary"
	assert final_round_state["slot_logs"][1]["reasoning_payload"] == {"effort": "none"}
	assert final_round_state["slot_logs"][2]["completion_tokens"] == 30
	assert final_round_state["slot_logs"][2]["reasoning_payload"] == {
		"effort": "high",
		"exclude": False,
	}


@pytest.mark.anyio
async def test_stream_all_models_emits_reasoning_payload_through_openrouter_request(
	monkeypatch,
) -> None:
	_stub_ui_helpers(monkeypatch)
	monkeypatch.setattr(app_module, "OPENROUTER_API_KEY", "test-api-key")
	monkeypatch.setattr(app_module, "_shuffled_display_order", lambda: [0, 1, 2])
	monkeypatch.setattr(
		app_module,
		"MODEL_LOOKUP",
		{
			"alpha/one": {
				"full_label": "Alpha One",
				"provider_key": "alpha",
				"supported_parameters": [],
			},
			"beta/two": {
				"full_label": "Beta Two",
				"provider_key": "beta",
				"supported_parameters": ["reasoning"],
			},
			"gamma/three": {
				"full_label": "Gamma Three",
				"provider_key": "gamma",
				"supported_parameters": ["reasoning"],
			},
		},
	)

	captured_payloads: list[dict[str, object]] = []

	class CapturingAsyncClient:
		def __init__(self, *args, **kwargs) -> None:
			self.args = args
			self.kwargs = kwargs

		async def __aenter__(self):
			return self

		async def __aexit__(self, exc_type, exc, tb) -> None:
			return None

		def stream(self, method, url, headers, json):
			captured_payloads.append(
				{
					"method": method,
					"url": url,
					"headers": headers,
					"json": json,
				}
			)
			return _CapturedStreamContext(_CapturedStreamResponse(json["model"]))

	monkeypatch.setattr(api_module.httpx, "AsyncClient", CapturingAsyncClient)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"beta/two",
		"gamma/three",
		None,
		"none",
		"high",
	)

	payloads_by_model = {payload["json"]["model"]: payload["json"] for payload in captured_payloads}
	final_round_state = outputs[-1][4]

	assert payloads_by_model["alpha/one"] == {
		"model": "alpha/one",
		"messages": [
			{"role": "system", "content": "System prompt"},
			{"role": "user", "content": "Compare models."},
		],
		"stream": True,
	}
	assert payloads_by_model["beta/two"]["reasoning"] == {"effort": "none"}
	assert payloads_by_model["gamma/three"]["reasoning"] == {
		"effort": "high",
		"exclude": False,
	}
	assert all(
		"model_entry" not in payload["json"] and "reasoning_payload" not in payload["json"]
		for payload in captured_payloads
	)
	assert final_round_state["ready_for_vote"] is True


@pytest.mark.anyio
async def test_stream_all_models_warns_when_selected_reasoning_is_unsupported(
	monkeypatch,
) -> None:
	_stub_ui_helpers(monkeypatch)
	monkeypatch.setattr(app_module, "OPENROUTER_API_KEY", "test-api-key")
	monkeypatch.setattr(app_module, "_shuffled_display_order", lambda: [0, 1, 2])
	monkeypatch.setattr(app_module, "MODEL_LOOKUP", {})
	requests = _fake_streaming_api(
		monkeypatch,
		[
			{"slot": 0, "delta": "Alpha answer"},
			{"slot": 0, "event": "complete", "usage": {}, "stats": {}},
			{"slot": 1, "delta": "Beta answer"},
			{"slot": 1, "event": "complete", "usage": {}, "stats": {}},
			{"slot": 2, "delta": "Gamma answer"},
			{"slot": 2, "event": "complete", "usage": {}, "stats": {}},
		],
	)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"beta/two",
		"gamma/three",
		None,
		"high",
		None,
	)

	final_round_state = outputs[-1][4]

	assert requests[1]["prompt_requests"][1]["reasoning_payload"] is None
	assert final_round_state["slot_logs"][1]["reasoning_payload"] is None
	assert final_round_state["slot_logs"][1]["final_response"] == "Beta answer"
	assert final_round_state["slot_logs"][1]["message_history"][1]["content"].startswith(
		"[Warning] Reasoning effort 'high' was selected"
	)
	assert final_round_state["slot_logs"][1]["message_history"][1]["metadata"] == {
		"title": "Warning",
		"status": "done",
	}


@pytest.mark.anyio
async def test_stream_all_models_marks_mixed_success_and_error_as_vote_ready(monkeypatch) -> None:
	_stub_ui_helpers(monkeypatch)
	monkeypatch.setattr(app_module, "OPENROUTER_API_KEY", "test-api-key")
	monkeypatch.setattr(app_module, "_shuffled_display_order", lambda: [0, 1, 2])
	_fake_streaming_api(
		monkeypatch,
		[
			{"slot": 0, "delta": "Alpha answer"},
			{"slot": 0, "event": "complete", "usage": {}, "stats": {}},
			{
				"slot": 1,
				"event": "reasoning",
				"reasoning_details": [{"type": "reasoning.summary", "summary": "Interrupted"}],
			},
			{"slot": 1, "event": "error", "error": "provider failure"},
			{"slot": 2, "delta": "Gamma answer"},
			{
				"slot": 2,
				"event": "complete",
				"usage": {"completion_tokens": 12},
				"stats": {"finish_reason": "stop"},
			},
		],
	)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"beta/two",
		"gamma/three",
	)

	final_round_state = outputs[-1][4]

	assert final_round_state["ready_for_vote"] is True
	assert final_round_state["completed_slots"] == [0, 2]
	assert final_round_state["errored_slots"] == [1]
	assert final_round_state["slot_logs"][1]["status"] == "error"
	assert final_round_state["slot_logs"][1]["error"] == "provider failure"


@pytest.mark.anyio
async def test_stream_all_models_recovers_from_background_stream_failure(monkeypatch) -> None:
	_stub_ui_helpers(monkeypatch)
	monkeypatch.setattr(app_module, "OPENROUTER_API_KEY", "test-api-key")
	monkeypatch.setattr(app_module, "_shuffled_display_order", lambda: [0, 1, 2])
	monkeypatch.setattr(api_module.httpx, "AsyncClient", _NoopAsyncClient)
	monkeypatch.setattr(app_module.OpenRouterAPI, "_prompt_model", _failing_prompt_model)

	outputs = await _collect_stream_outputs(
		"Compare models.",
		"System prompt",
		"alpha/one",
		"beta/two",
		"gamma/three",
	)

	final_round_state = outputs[-1][4]

	assert len(outputs) >= 2
	assert final_round_state["generation_completed_at"] is not None
	assert final_round_state["ready_for_vote"] is False
	assert final_round_state["vote_stage"] == "unavailable"
	assert final_round_state["completed_slots"] == []
	assert final_round_state["errored_slots"] == [0, 1, 2]
	assert final_round_state["slot_logs"][0]["final_response"] == "first chunk"
	assert final_round_state["slot_logs"][0]["status"] == "error"
	error_messages = [
		message
		for message in final_round_state["slot_logs"][0]["message_history"]
		if message.get("metadata", {}).get("title") == "Generation Error"
	]
	assert len(error_messages) == 1
	assert error_messages[0]["content"].startswith("[Error] Generation stopped")
	assert (
		final_round_state["slot_logs"][0]["error"]
		== "Generation stopped before this round could finish."
	)
