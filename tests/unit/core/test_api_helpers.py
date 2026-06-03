import httpx
import pytest

from arena.core.api import (
	OpenRouterAPI,
	_legacy_reasoning_details,
	_merge_reasoning_details,
	_normalize_reasoning_details,
	_to_float,
)


def test_to_float_returns_float_or_none() -> None:
	assert _to_float("3.5") == 3.5
	assert _to_float(2) == 2.0
	assert _to_float("not-a-number") is None


def test_normalize_reasoning_details_accepts_dicts_and_lists() -> None:
	assert _normalize_reasoning_details({"type": "reasoning.text"}) == [{"type": "reasoning.text"}]
	assert _normalize_reasoning_details([{"type": "reasoning.summary"}, "ignore-me"]) == [
		{"type": "reasoning.summary"}
	]
	assert _normalize_reasoning_details("invalid") == []


def test_merge_reasoning_details_merges_incremental_text_and_preserves_metadata() -> None:
	existing = [
		{
			"id": "detail-1",
			"index": 0,
			"type": "reasoning.text",
			"text": "Hello",
			"format": "markdown",
		}
	]
	incoming = [
		{
			"id": "detail-1",
			"index": 0,
			"type": "reasoning.text",
			"text": "Hello world",
			"format": "",
		},
		{
			"id": "detail-2",
			"index": 1,
			"type": "reasoning.summary",
			"summary": "Short summary",
		},
	]

	merged = _merge_reasoning_details(existing, incoming)

	assert merged[0]["text"] == "Hello world"
	assert merged[0]["format"] == "markdown"
	assert merged[1]["summary"] == "Short summary"


def test_legacy_reasoning_details_builds_text_block_with_index() -> None:
	legacy_details = _legacy_reasoning_details({"reasoning": "chain of thought"}, start_index=4)

	assert legacy_details == [
		{
			"type": "reasoning.text",
			"text": "chain of thought",
			"format": "unknown",
			"index": 4,
		}
	]


def test_normalize_model_catalog_filters_non_text_models_and_sorts_results() -> None:
	models = [
		{
			"id": "zeta/image-model",
			"name": "Zeta: Image Model",
			"architecture": {
				"input_modalities": ["image"],
				"output_modalities": ["text"],
			},
		},
		{
			"id": "beta/plain_text",
			"architecture": {
				"input_modalities": ["text"],
				"output_modalities": ["text"],
			},
		},
		{
			"id": "alpha/chat-model",
			"name": "Alpha: Chat Model",
			"architecture": {
				"input_modalities": ["text"],
				"output_modalities": ["text"],
			},
		},
		{"id": "missing-slash", "name": "Ignore me", "architecture": {}},
	]

	normalized = OpenRouterAPI.normalize_model_catalog(models)

	assert [entry["model_id"] for entry in normalized] == ["alpha/chat-model", "beta/plain_text"]
	assert normalized[1]["provider_label"] == "Beta"
	assert normalized[1]["model_label"] == "plain text"


def test_get_key_info_fetches_openrouter_key_metadata(monkeypatch) -> None:
	requests: list[dict[str, object]] = []

	class FakeResponse:
		def raise_for_status(self) -> None:
			requests.append({"raised": True})

		def json(self) -> dict[str, object]:
			return {"data": {"label": "test key"}}

	class FakeClient:
		def __enter__(self) -> "FakeClient":
			return self

		def __exit__(self, exc_type, exc, traceback) -> None:
			return None

		def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
			requests.append({"url": url, "headers": headers})
			return FakeResponse()

	monkeypatch.setattr(httpx, "Client", FakeClient)

	api = OpenRouterAPI(api_key="test-api-key")

	assert api.get_key_info() == {"data": {"label": "test key"}}
	assert requests == [
		{
			"url": "https://openrouter.ai/api/v1/key",
			"headers": api.headers,
		},
		{"raised": True},
	]


def test_normalize_prompt_requests_accepts_strings_and_dicts() -> None:
	requests = OpenRouterAPI._normalize_prompt_requests(
		[
			"alpha/one",
			{"model": "beta/two", "slot": 7, "temperature": 0.2},
		]
	)

	assert requests == [
		{"slot": 0, "model": "alpha/one"},
		{"model": "beta/two", "slot": 7, "temperature": 0.2},
	]


def test_normalize_prompt_requests_rejects_invalid_entries() -> None:
	with pytest.raises(TypeError):
		OpenRouterAPI._normalize_prompt_requests([123])

	with pytest.raises(ValueError):
		OpenRouterAPI._normalize_prompt_requests([{"model": ""}])
