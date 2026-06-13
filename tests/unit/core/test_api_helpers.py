import httpx
import pytest

from arena.core.api import (
	OpenRouterAPI,
	_legacy_reasoning_details,
	_merge_reasoning_details,
	_normalize_reasoning_details,
	_to_float,
)
from arena.core.reasoning import normalize_reasoning_payload, reasoning_capabilities_for_model


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


def test_normalize_model_catalog_preserves_openrouter_metadata() -> None:
	models = [
		{
			"id": "alpha/reasoner",
			"name": "Alpha: Reasoner",
			"architecture": {
				"input_modalities": ["text"],
				"output_modalities": ["text"],
			},
			"supported_parameters": ["reasoning", "reasoning.max_tokens"],
			"default_parameters": {"reasoning": {"max_tokens": 2048}},
			"top_provider": {"max_completion_tokens": 8192},
			"pricing": {"prompt": "0.000001", "completion": "0.000002"},
		}
	]

	normalized = OpenRouterAPI.normalize_model_catalog(models)

	assert normalized == [
		{
			"model_id": "alpha/reasoner",
			"provider_key": "alpha",
			"provider_label": "Alpha",
			"model_label": "Reasoner",
			"full_label": "Alpha: Reasoner",
			"supported_parameters": ["reasoning", "reasoning.max_tokens"],
			"default_parameters": {"reasoning": {"max_tokens": 2048}},
			"top_provider": {"max_completion_tokens": 8192},
			"pricing": {"prompt": "0.000001", "completion": "0.000002"},
			"reasoning_capabilities": {
				"supported": True,
				"control_type": "budget",
				"supports_effort": False,
				"effort_choices": [],
				"default_effort": None,
				"supports_max_tokens": True,
				"default_max_tokens": 2048,
				"max_reasoning_tokens": 7372,
				"pricing": {"prompt": "0.000001", "completion": "0.000002"},
			},
		}
	]


def test_reasoning_capabilities_are_hidden_without_supported_metadata() -> None:
	capabilities = reasoning_capabilities_for_model({"supported_parameters": []})

	assert capabilities["supported"] is False
	assert capabilities["control_type"] == "none"
	assert capabilities["effort_choices"] == []
	assert capabilities["max_reasoning_tokens"] is None


def test_reasoning_capabilities_do_not_infer_support_from_defaults() -> None:
	capabilities = reasoning_capabilities_for_model(
		{"default_parameters": {"reasoning": {"max_tokens": 4096}}}
	)

	assert capabilities["supported"] is False
	assert capabilities["control_type"] == "none"
	assert capabilities["default_max_tokens"] is None
	assert capabilities["max_reasoning_tokens"] is None


def test_reasoning_capabilities_use_toggle_for_generic_reasoning_support() -> None:
	capabilities = reasoning_capabilities_for_model({"supported_parameters": ["reasoning"]})

	assert capabilities["supported"] is True
	assert capabilities["control_type"] == "toggle"
	assert capabilities["supports_effort"] is False
	assert capabilities["supports_max_tokens"] is False


def test_reasoning_capabilities_favor_effort_controls_over_token_budget() -> None:
	capabilities = reasoning_capabilities_for_model(
		{
			"supported_parameters": ["reasoning", "reasoning.effort", "reasoning.max_tokens"],
			"default_parameters": {"reasoning_effort": "low"},
			"top_provider": {"max_completion_tokens": 100_000},
			"pricing": {"internal_reasoning": "0.000003"},
		}
	)

	assert capabilities["control_type"] == "effort"
	assert capabilities["effort_choices"] == ["none", "low", "medium", "high"]
	assert capabilities["default_effort"] == "low"
	assert capabilities["supports_max_tokens"] is True
	assert capabilities["max_reasoning_tokens"] == 90_000
	assert capabilities["pricing"] == {"internal_reasoning": "0.000003"}


def test_reasoning_capabilities_allow_large_token_budgets_from_provider_metadata() -> None:
	capabilities = reasoning_capabilities_for_model(
		{
			"supported_parameters": ["reasoning.max_tokens"],
			"top_provider": {"max_completion_tokens": 200_000},
		}
	)

	assert capabilities["control_type"] == "budget"
	assert capabilities["max_reasoning_tokens"] == 180_000


def test_reasoning_capabilities_use_toggle_when_budget_ceiling_is_missing() -> None:
	capabilities = reasoning_capabilities_for_model(
		{
			"supported_parameters": ["reasoning", "reasoning.max_tokens"],
			"default_parameters": {"reasoning": {"max_tokens": 4096}},
		}
	)

	assert capabilities["supported"] is True
	assert capabilities["control_type"] == "toggle"
	assert capabilities["supports_max_tokens"] is True
	assert capabilities["default_max_tokens"] is None
	assert capabilities["max_reasoning_tokens"] is None


def test_normalize_reasoning_payload_omits_unsupported_models() -> None:
	payload = normalize_reasoning_payload(
		{"supported_parameters": []},
		{"enabled": True, "max_tokens": 1024, "effort": "high"},
	)

	assert payload is None


def test_normalize_reasoning_payload_builds_effort_payloads() -> None:
	model = {"supported_parameters": ["reasoning.effort"]}

	assert normalize_reasoning_payload(model, {"effort": "high"}) == {
		"effort": "high",
		"exclude": False,
	}
	assert normalize_reasoning_payload(model, {"effort": "none"}) == {"effort": "none"}
	assert normalize_reasoning_payload(model, {"effort": "extreme"}) is None


def test_normalize_reasoning_payload_builds_budget_payloads() -> None:
	model = {
		"supported_parameters": ["reasoning.max_tokens"],
		"top_provider": {"max_completion_tokens": 10_000},
	}

	assert normalize_reasoning_payload(model, {"enabled": True, "max_tokens": 9500}) == {
		"max_tokens": 9000,
		"exclude": False,
	}
	assert normalize_reasoning_payload(model, {"enabled": False, "max_tokens": 4096}) is None


def test_normalize_reasoning_payload_uses_toggle_when_budget_ceiling_is_missing() -> None:
	model = {"supported_parameters": ["reasoning", "reasoning.max_tokens"]}

	assert normalize_reasoning_payload(model, {"enabled": True, "max_tokens": 4096}) == {
		"enabled": True,
		"exclude": False,
	}


def test_request_payload_params_merge_shared_and_request_specific_values() -> None:
	params = OpenRouterAPI._request_payload_params(
		{
			"slot": 0,
			"model": "alpha/one",
			"temperature": 0.7,
			"params": {"top_p": 0.8, "temperature": 0.5},
		},
		{"temperature": 0.2, "max_tokens": 1000},
	)

	assert params == {"temperature": 0.7, "max_tokens": 1000, "top_p": 0.8}


def test_request_payload_params_sanitizes_reasoning_from_model_metadata() -> None:
	params = OpenRouterAPI._request_payload_params(
		{
			"slot": 0,
			"model": "alpha/one",
			"model_entry": {
				"supported_parameters": ["reasoning.effort", "reasoning.max_tokens"],
				"top_provider": {"max_completion_tokens": 20_000},
			},
			"reasoning_settings": {"effort": "medium", "max_tokens": 18_000},
			"reasoning": {"max_tokens": 18_000},
		},
		{"reasoning": {"enabled": True}},
	)

	assert params == {"reasoning": {"effort": "medium", "exclude": False}}


def test_request_payload_params_removes_shared_reasoning_for_unsupported_model() -> None:
	params = OpenRouterAPI._request_payload_params(
		{
			"slot": 0,
			"model": "alpha/one",
			"model_entry": {"supported_parameters": []},
			"reasoning_settings": {"enabled": True},
		},
		{"reasoning": {"enabled": True}, "temperature": 0.2},
	)

	assert params == {"temperature": 0.2}


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
