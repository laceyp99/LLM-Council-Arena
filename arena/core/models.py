import os
from typing import Any

from dotenv import load_dotenv

from arena.core.api import OpenRouterAPI
from arena.core.reasoning import reasoning_capabilities_for_model


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
			"openai/gpt-5.4-mini",
			"openai",
			"OpenAI",
			"GPT-5.4 Mini",
			"OpenAI: GPT-5.4 Mini",
		),
		_fallback_entry(
			"anthropic/claude-sonnet-4.5",
			"anthropic",
			"Anthropic",
			"Claude Sonnet 4.5",
			"Anthropic: Claude Sonnet 4.5",
		),
		_fallback_entry(
			"google/gemini-3.1-flash-lite-preview",
			"google",
			"Google",
			"Gemini 3.1 Flash Lite Preview",
			"Google: Gemini 3.1 Flash Lite Preview",
		),
	]


def _load_model_catalog(
	site_url: str, site_name: str
) -> tuple[list[dict[str, Any]], str, str | None]:
	load_dotenv()
	api_key = os.getenv("OPENROUTER_API_KEY")

	if not api_key:
		return (
			_fallback_model_catalog(),
			"Warning: OPENROUTER_API_KEY is missing. Using the fallback model list until an API key is configured.",
			api_key,
		)

	api = OpenRouterAPI(api_key=api_key, site_url=site_url, site_name=site_name)

	try:
		api.get_key_info()
	except Exception as exc:
		return (
			_fallback_model_catalog(),
			f"Warning: could not validate OPENROUTER_API_KEY ({exc}). Using the fallback model list.",
			api_key,
		)

	try:
		catalog = api.get_normalized_text_models()
		if not catalog:
			raise RuntimeError("OpenRouter returned no text-capable models.")
		return catalog, f"Loaded {len(catalog)} text-capable models from OpenRouter.", api_key
	except Exception as exc:
		return (
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


def _chatbot_label(model_lookup: dict[str, dict[str, Any]], model_id: str) -> str:
	entry = model_lookup.get(model_id)
	if entry:
		return entry["full_label"]
	return model_id or "Model"


def _provider_for_model(
	model_lookup: dict[str, dict[str, Any]],
	provider_choices: list[tuple[str, str]],
	model_id: str,
) -> str:
	entry = model_lookup.get(model_id)
	if entry:
		return entry["provider_key"]
	if model_id and "/" in model_id:
		return model_id.split("/", 1)[0]
	return provider_choices[0][1] if provider_choices else ""


def _model_choices_for_provider(
	provider_models: dict[str, list[dict[str, Any]]],
	model_catalog: list[dict[str, Any]],
	provider_key: str,
) -> list[tuple[str, str]]:
	provider_entries = provider_models.get(provider_key) or model_catalog
	return [(entry["model_label"], entry["model_id"]) for entry in provider_entries]


def _resolve_model_for_provider(
	provider_models: dict[str, list[dict[str, Any]]],
	model_catalog: list[dict[str, Any]],
	provider_key: str,
	model_id: str | None = None,
) -> str:
	provider_entries = provider_models.get(provider_key) or model_catalog
	if not provider_entries:
		return ""

	if model_id and any(entry["model_id"] == model_id for entry in provider_entries):
		return model_id

	return provider_entries[0]["model_id"]
