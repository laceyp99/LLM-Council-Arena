import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from arena.core.api import OpenRouterAPI
from arena.core.reasoning import reasoning_capabilities_for_model
from arena.ui.config import DEFAULT_MODEL_IDS, PANEL_COUNT


def _fallback_entry(
	model_id: str,
	provider_key: str,
	provider_label: str,
	model_label: str,
	full_label: str,
) -> dict[str, Any]:
	entry: dict[str, Any] = {
		"model_id": model_id,
		"provider_key": provider_key,
		"provider_label": provider_label,
		"model_label": model_label,
		"full_label": full_label,
		"supported_parameters": [],
		"default_parameters": {},
		"top_provider": {},
		"pricing": {},
	}
	entry["reasoning_capabilities"] = reasoning_capabilities_for_model(entry)
	return entry


def _fallback_model_catalog() -> list[dict[str, Any]]:
	return [
		_fallback_entry(
			"google/gemini-3.5-flash-lite",
			"google",
			"Google",
			"Gemini 3.5 Flash Lite",
			"Google: Gemini 3.5 Flash Lite",
		),
		_fallback_entry(
			"openai/gpt-5.6-luna",
			"openai",
			"OpenAI",
			"GPT-5.6 Luna",
			"OpenAI: GPT-5.6 Luna",
		),
		_fallback_entry(
			"anthropic/claude-sonnet-5",
			"anthropic",
			"Anthropic",
			"Claude Sonnet 5",
			"Anthropic: Claude Sonnet 5",
		),
	]


@dataclass(frozen=True)
class CatalogState:
	"""One consistent snapshot of the model catalog and everything derived from it."""

	catalog: list[dict[str, Any]] = field(repr=False)
	status: str
	api_key: str | None = field(default=None, repr=False)
	# True only when the live OpenRouter catalog loaded; gates the Arena UI.
	ready: bool = False
	lookup: dict[str, dict[str, Any]] = field(init=False, repr=False)
	provider_choices: list[tuple[str, str]] = field(init=False, repr=False)
	provider_models: dict[str, list[dict[str, Any]]] = field(init=False, repr=False)
	default_panel_model_ids: list[str] = field(init=False, repr=False)

	def __post_init__(self) -> None:
		# Frozen, so derived fields are set once here.
		provider_choices, provider_models = _build_provider_index(self.catalog)
		object.__setattr__(self, "lookup", {entry["model_id"]: entry for entry in self.catalog})
		object.__setattr__(self, "provider_choices", provider_choices)
		object.__setattr__(self, "provider_models", provider_models)
		object.__setattr__(
			self,
			"default_panel_model_ids",
			_default_model_ids(self.catalog, provider_models, DEFAULT_MODEL_IDS, PANEL_COUNT),
		)

	def chatbot_label(self, model_id: str) -> str:
		entry = self.lookup.get(model_id)
		if entry:
			return entry["full_label"]
		return model_id or "Model"

	def provider_for_model(self, model_id: str) -> str:
		entry = self.lookup.get(model_id)
		if entry:
			return entry["provider_key"]
		if model_id and "/" in model_id:
			return model_id.split("/", 1)[0]
		return self.provider_choices[0][1] if self.provider_choices else ""

	def model_choices_for_provider(self, provider_key: str) -> list[tuple[str, str]]:
		provider_entries = self.provider_models.get(provider_key) or self.catalog
		return [(entry["model_label"], entry["model_id"]) for entry in provider_entries]

	def resolve_model_for_provider(self, provider_key: str, model_id: str | None = None) -> str:
		provider_entries = self.provider_models.get(provider_key) or self.catalog
		if not provider_entries:
			return ""

		if model_id and any(entry["model_id"] == model_id for entry in provider_entries):
			return model_id

		return provider_entries[0]["model_id"]


def _load_model_catalog(site_url: str, site_name: str) -> CatalogState:
	load_dotenv()
	api_key = os.getenv("OPENROUTER_API_KEY")

	if not api_key:
		return CatalogState(
			_fallback_model_catalog(),
			"Warning: OPENROUTER_API_KEY is missing. Using the fallback model list until an API key is configured.",
			api_key,
		)

	api = OpenRouterAPI(api_key=api_key, site_url=site_url, site_name=site_name)

	try:
		api.get_key_info()
	except Exception as exc:
		return CatalogState(
			_fallback_model_catalog(),
			f"Warning: could not validate OPENROUTER_API_KEY ({exc}). Using the fallback model list.",
			api_key,
		)

	try:
		catalog = api.get_normalized_text_models()
		if not catalog:
			raise RuntimeError("OpenRouter returned no text-capable models.")
		return CatalogState(
			catalog,
			f"Loaded {len(catalog)} text-capable models from OpenRouter.",
			api_key,
			ready=True,
		)
	except Exception as exc:
		return CatalogState(
			_fallback_model_catalog(),
			f"Warning: could not load the live OpenRouter catalog ({exc}). Using the fallback model list.",
			api_key,
		)


def _build_provider_index(
	model_catalog: list[dict[str, Any]],
) -> tuple[list[tuple[str, str]], dict[str, list[dict[str, Any]]]]:
	provider_choices: list[tuple[str, str]] = []
	provider_models: dict[str, list[dict[str, str]]] = {}

	for entry in model_catalog:
		provider_key = entry["provider_key"]
		if provider_key not in provider_models:
			provider_models[provider_key] = []
			provider_choices.append((entry["provider_label"], provider_key))
		provider_models[provider_key].append(entry)

	return provider_choices, provider_models


def _default_model_ids(
	model_catalog: list[dict[str, Any]],
	provider_models: dict[str, list[dict[str, Any]]],
	default_model_ids: list[str],
	panel_count: int,
) -> list[str]:
	if not model_catalog:
		return ["" for _ in range(panel_count)]

	available_ids = {entry["model_id"] for entry in model_catalog}
	resolved_default_ids: list[str] = []

	for preferred_id in default_model_ids[:panel_count]:
		if preferred_id in available_ids:
			resolved_default_ids.append(preferred_id)
			continue

		provider_key = preferred_id.split("/", 1)[0]
		provider_entries = provider_models.get(provider_key) or model_catalog
		resolved_default_ids.append(provider_entries[0]["model_id"])

	while len(resolved_default_ids) < panel_count:
		resolved_default_ids.append(
			model_catalog[len(resolved_default_ids) % len(model_catalog)]["model_id"]
		)

	return resolved_default_ids
