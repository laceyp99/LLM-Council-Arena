from arena.core import models as model_module
from arena.core.models import (
	_build_provider_index,
	_chatbot_label,
	_default_model_ids,
	_fallback_model_catalog,
	_load_model_catalog,
	_model_choices_for_provider,
	_provider_for_model,
	_resolve_model_for_provider,
)


def test_fallback_model_catalog_has_expected_entries() -> None:
	catalog = _fallback_model_catalog()

	assert [entry["provider_key"] for entry in catalog] == ["openai", "anthropic", "google"]
	assert all(
		{"model_id", "provider_key", "provider_label", "model_label", "full_label"} <= set(entry)
		for entry in catalog
	)
	assert all(entry["reasoning_capabilities"]["supported"] is False for entry in catalog)


def test_build_provider_index_groups_models_and_preserves_first_seen_order() -> None:
	model_catalog = [
		{
			"model_id": "alpha/one",
			"provider_key": "alpha",
			"provider_label": "Alpha",
			"model_label": "One",
			"full_label": "Alpha: One",
		},
		{
			"model_id": "beta/two",
			"provider_key": "beta",
			"provider_label": "Beta",
			"model_label": "Two",
			"full_label": "Beta: Two",
		},
		{
			"model_id": "alpha/three",
			"provider_key": "alpha",
			"provider_label": "Alpha",
			"model_label": "Three",
			"full_label": "Alpha: Three",
		},
	]

	provider_choices, provider_models = _build_provider_index(model_catalog)

	assert provider_choices == [("Alpha", "alpha"), ("Beta", "beta")]
	assert [entry["model_id"] for entry in provider_models["alpha"]] == [
		"alpha/one",
		"alpha/three",
	]
	assert [entry["model_id"] for entry in provider_models["beta"]] == ["beta/two"]


def test_default_model_ids_fall_back_to_provider_and_cycle_to_fill_slots() -> None:
	model_catalog = [
		{"model_id": "alpha/one", "provider_key": "alpha"},
		{"model_id": "beta/two", "provider_key": "beta"},
	]
	provider_models = {
		"alpha": [model_catalog[0]],
		"beta": [model_catalog[1]],
	}

	resolved_ids = _default_model_ids(
		model_catalog=model_catalog,
		provider_models=provider_models,
		default_model_ids=["alpha/one", "beta/missing"],
		panel_count=4,
	)

	assert resolved_ids == ["alpha/one", "beta/two", "alpha/one", "beta/two"]


def test_model_lookup_helpers_fall_back_cleanly() -> None:
	model_lookup = {
		"alpha/one": {"full_label": "Alpha: One", "provider_key": "alpha"},
	}
	provider_choices = [("Alpha", "alpha"), ("Beta", "beta")]
	provider_models = {
		"alpha": [{"model_label": "One", "model_id": "alpha/one"}],
		"beta": [{"model_label": "Two", "model_id": "beta/two"}],
	}
	model_catalog = [{"model_label": "Fallback", "model_id": "fallback/model"}]

	assert _chatbot_label(model_lookup, "alpha/one") == "Alpha: One"
	assert _chatbot_label(model_lookup, "") == "Model"
	assert _provider_for_model(model_lookup, provider_choices, "beta/two") == "beta"
	assert _provider_for_model(model_lookup, provider_choices, "") == "alpha"
	assert _model_choices_for_provider(provider_models, model_catalog, "beta") == [
		("Two", "beta/two")
	]
	assert _resolve_model_for_provider(provider_models, model_catalog, "beta") == "beta/two"
	assert (
		_resolve_model_for_provider(provider_models, model_catalog, "beta", "beta/two")
		== "beta/two"
	)


def test_load_model_catalog_without_api_key_uses_fallback(monkeypatch) -> None:
	monkeypatch.setattr(model_module, "load_dotenv", lambda: None)
	monkeypatch.setattr(model_module.os, "getenv", lambda key: None)

	catalog, status, api_key = _load_model_catalog(
		site_url="http://localhost:7860",
		site_name="LLM Council Arena",
	)

	assert catalog == _fallback_model_catalog()
	assert "missing" in status.lower()
	assert api_key is None


def test_load_model_catalog_with_api_key_returns_live_catalog(monkeypatch) -> None:
	class FakeOpenRouterAPI:
		def __init__(self, api_key: str, site_url: str, site_name: str) -> None:
			self.api_key = api_key
			self.site_url = site_url
			self.site_name = site_name

		def get_key_info(self) -> dict[str, object]:
			return {"label": "test key"}

		def get_normalized_text_models(self) -> list[dict[str, str]]:
			return [
				{
					"model_id": "alpha/one",
					"provider_key": "alpha",
					"provider_label": "Alpha",
					"model_label": "One",
					"full_label": "Alpha: One",
				}
			]

	monkeypatch.setattr(model_module, "load_dotenv", lambda: None)
	monkeypatch.setattr(model_module.os, "getenv", lambda key: "test-api-key")
	monkeypatch.setattr(model_module, "OpenRouterAPI", FakeOpenRouterAPI)

	catalog, status, api_key = _load_model_catalog(
		site_url="http://localhost:7860",
		site_name="LLM Council Arena",
	)

	assert catalog == [
		{
			"model_id": "alpha/one",
			"provider_key": "alpha",
			"provider_label": "Alpha",
			"model_label": "One",
			"full_label": "Alpha: One",
		}
	]
	assert "loaded 1 text-capable models" in status.lower()
	assert api_key == "test-api-key"


def test_load_model_catalog_with_api_key_falls_back_on_catalog_error(monkeypatch) -> None:
	class FakeOpenRouterAPI:
		def __init__(self, api_key: str, site_url: str, site_name: str) -> None:
			self.api_key = api_key

		def get_key_info(self) -> dict[str, object]:
			return {"label": "test key"}

		def get_normalized_text_models(self) -> list[dict[str, str]]:
			raise RuntimeError("upstream unavailable")

	monkeypatch.setattr(model_module, "load_dotenv", lambda: None)
	monkeypatch.setattr(model_module.os, "getenv", lambda key: "test-api-key")
	monkeypatch.setattr(model_module, "OpenRouterAPI", FakeOpenRouterAPI)

	catalog, status, api_key = _load_model_catalog(
		site_url="http://localhost:7860",
		site_name="LLM Council Arena",
	)

	assert catalog == _fallback_model_catalog()
	assert "could not load the live openrouter catalog" in status.lower()
	assert api_key == "test-api-key"


def test_load_model_catalog_with_api_key_falls_back_on_key_validation_error(monkeypatch) -> None:
	class FakeOpenRouterAPI:
		def __init__(self, api_key: str, site_url: str, site_name: str) -> None:
			self.api_key = api_key

		def get_key_info(self) -> dict[str, object]:
			raise RuntimeError("invalid key")

		def get_normalized_text_models(self) -> list[dict[str, str]]:
			raise AssertionError("catalog should not load when key validation fails")

	monkeypatch.setattr(model_module, "load_dotenv", lambda: None)
	monkeypatch.setattr(model_module.os, "getenv", lambda key: "test-api-key")
	monkeypatch.setattr(model_module, "OpenRouterAPI", FakeOpenRouterAPI)

	catalog, status, api_key = _load_model_catalog(
		site_url="http://localhost:7860",
		site_name="LLM Council Arena",
	)

	assert catalog == _fallback_model_catalog()
	assert "could not validate openrouter_api_key" in status.lower()
	assert api_key == "test-api-key"
