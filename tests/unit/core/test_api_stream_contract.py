import httpx
import pytest

from arena.core import api as api_module


class _FakeStreamResponse:
	is_error = False

	def __init__(self, lines: list[str]) -> None:
		self._lines = lines

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc, tb) -> None:
		return None

	async def aiter_lines(self):
		for line in self._lines:
			yield line


class _FakeAsyncClient:
	def __init__(self, response: _FakeStreamResponse) -> None:
		self._response = response
		self.calls: list[dict[str, object]] = []

	def stream(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, object]):
		self.calls.append(
			{
				"method": method,
				"url": url,
				"headers": headers,
				"json": json,
			}
		)
		return self._response


class _NoopAsyncClient:
	def __init__(self, *args, **kwargs) -> None:
		self.args = args
		self.kwargs = kwargs

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc, tb) -> None:
		return None


async def _collect_prompt_chunks(
	lines: list[str], monkeypatch
) -> tuple[list[dict], list[dict[str, object]]]:
	perf_values = iter([10.0, 10.5, 11.0])
	monkeypatch.setattr(api_module.time, "perf_counter", lambda: next(perf_values))
	response = _FakeStreamResponse(lines)
	client = _FakeAsyncClient(response)
	api = api_module.OpenRouterAPI(api_key="test-api-key")
	chunks = [
		chunk
		async for chunk in api._prompt_model(
			client,
			{"slot": 1, "model": "alpha/one"},
			[{"role": "user", "content": "Compare models."}],
			reasoning={"effort": "medium"},
		)
	]
	return chunks, client.calls


@pytest.mark.anyio
async def test_prompt_model_merges_reasoning_and_emits_completion_stats(monkeypatch) -> None:
	chunks, calls = await _collect_prompt_chunks(
		[
			'data: {"choices":[{"delta":{"reasoning_details":[{"id":"r1","index":0,"type":"reasoning.text","text":"Hello"}]}}]}',
			'data: {"choices":[{"delta":{"reasoning_details":[{"id":"r1","index":0,"type":"reasoning.text","text":"Hello world"}]}}]}',
			'data: {"choices":[{"delta":{"content":"Answer"}}]}',
			'data: {"usage":{"completion_tokens":4,"cost":0.001},"choices":[{"finish_reason":"stop","delta":{}}]}',
			"data: [DONE]",
		],
		monkeypatch,
	)

	assert calls[0]["method"] == "POST"
	assert calls[0]["json"]["model"] == "alpha/one"
	assert calls[0]["json"]["reasoning"] == {"effort": "medium"}
	assert chunks[0]["event"] == "reasoning"
	assert chunks[1]["event"] == "reasoning"
	assert chunks[1]["reasoning_details"][0]["text"] == "Hello world"
	assert chunks[2]["slot"] == 1
	assert chunks[2]["model"] == "alpha/one"
	assert chunks[2]["delta"] == "Answer"
	assert chunks[3]["event"] == "complete"
	assert chunks[3]["usage"] == {"completion_tokens": 4, "cost": 0.001}
	assert chunks[3]["stats"] == {
		"time_to_first_token": 0.5,
		"total_generation_time": 1.0,
		"tokens_per_second": 8.0,
		"finish_reason": "stop",
	}


@pytest.mark.anyio
async def test_prompt_model_prefers_request_specific_payload_params(monkeypatch) -> None:
	perf_values = iter([10.0, 10.5])
	monkeypatch.setattr(api_module.time, "perf_counter", lambda: next(perf_values))
	response = _FakeStreamResponse(
		[
			'data: {"usage":{"completion_tokens":1},"choices":[{"finish_reason":"stop","delta":{}}]}',
			"data: [DONE]",
		]
	)
	client = _FakeAsyncClient(response)
	api = api_module.OpenRouterAPI(api_key="test-api-key")

	chunks = [
		chunk
		async for chunk in api._prompt_model(
			client,
			{
				"slot": 1,
				"model": "alpha/one",
				"temperature": 0.8,
				"model_entry": {
					"supported_parameters": ["reasoning.max_tokens"],
					"top_provider": {"max_completion_tokens": 10_000},
				},
				"reasoning_payload": None,
			},
			[{"role": "user", "content": "Compare models."}],
			temperature=0.2,
			reasoning={"enabled": True},
		)
	]

	assert client.calls[0]["json"]["temperature"] == 0.8
	assert "reasoning" not in client.calls[0]["json"]
	assert chunks[0]["event"] == "complete"


@pytest.mark.anyio
async def test_prompt_model_uses_legacy_reasoning_and_skips_malformed_lines(monkeypatch) -> None:
	chunks, _ = await _collect_prompt_chunks(
		[
			"data: {not-json}",
			'data: {"choices":[{"delta":{"reasoning":"legacy chain"}}]}',
			'data: {"usage":{"completion_tokens":2},"choices":[{"finish_reason":"stop","delta":{}}]}',
			"data: [DONE]",
		],
		monkeypatch,
	)

	assert len(chunks) == 2
	assert chunks[0]["event"] == "reasoning"
	assert chunks[0]["reasoning_details"] == [
		{
			"type": "reasoning.text",
			"text": "legacy chain",
			"format": "unknown",
			"index": 0,
		}
	]
	assert chunks[1]["event"] == "complete"
	assert chunks[1]["reasoning_details"] == chunks[0]["reasoning_details"]


@pytest.mark.anyio
async def test_prompt_model_stops_after_provider_error_payload(monkeypatch) -> None:
	chunks, _ = await _collect_prompt_chunks(
		[
			'data: {"error":{"message":"provider unavailable"}}',
			'data: {"usage":{"completion_tokens":99},"choices":[{"finish_reason":"stop","delta":{}}]}',
			"data: [DONE]",
		],
		monkeypatch,
	)

	assert chunks == [
		{
			"event": "error",
			"slot": 1,
			"model": "alpha/one",
			"error": "provider unavailable",
			"response": {"error": {"message": "provider unavailable"}},
		}
	]


@pytest.mark.anyio
async def test_prompt_model_reports_http_error_body() -> None:
	transport = httpx.MockTransport(
		lambda request: httpx.Response(
			402, json={"error": {"code": 402, "message": "Insufficient credits"}}
		)
	)
	api = api_module.OpenRouterAPI(api_key="test-api-key")

	async with httpx.AsyncClient(transport=transport) as client:
		chunks = [
			chunk
			async for chunk in api._prompt_model(
				client,
				{"slot": 2, "model": "beta/two"},
				[{"role": "user", "content": "Compare models."}],
			)
		]

	assert chunks == [
		{
			"event": "error",
			"slot": 2,
			"model": "beta/two",
			"error": "HTTP 402 Payment Required: Insufficient credits",
		}
	]


async def _prompt_model_failing_on_slot_zero(self, client, request, messages, **kwargs):
	yield {"slot": request["slot"], "model": request["model"], "delta": "first chunk"}
	if request["slot"] == 0:
		raise RuntimeError("producer exploded")
	yield {"event": "complete", "slot": request["slot"], "model": request["model"]}


@pytest.mark.anyio
async def test_prompt_models_concurrent_reports_failure_and_keeps_sibling_chunks(
	monkeypatch,
) -> None:
	monkeypatch.setattr(api_module.httpx, "AsyncClient", _NoopAsyncClient)
	monkeypatch.setattr(
		api_module.OpenRouterAPI, "_prompt_model", _prompt_model_failing_on_slot_zero
	)
	api = api_module.OpenRouterAPI(api_key="test-api-key")

	chunks = [
		chunk
		async for chunk in api.prompt_models_concurrent(
			["alpha/one", "beta/two"],
			[{"role": "user", "content": "Compare models."}],
		)
	]

	def chunks_for(slot: int) -> list[dict]:
		return [chunk for chunk in chunks if chunk["slot"] == slot]

	assert chunks_for(0) == [
		{"slot": 0, "model": "alpha/one", "delta": "first chunk"},
		{"event": "error", "slot": 0, "model": "alpha/one", "error": "producer exploded"},
	]
	assert chunks_for(1) == [
		{"slot": 1, "model": "beta/two", "delta": "first chunk"},
		{"event": "complete", "slot": 1, "model": "beta/two"},
	]
