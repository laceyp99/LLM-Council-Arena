from decimal import Decimal, InvalidOperation
from typing import Any

OPENROUTER_REASONING_EFFORT_CHOICES = ["none", "minimal", "low", "medium", "high", "xhigh"]
REASONING_TOKEN_BUDGET_OUTPUT_RATIO = 0.9


def _as_dict(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, dict) else {}


def _as_string_list(value: Any) -> list[str]:
	if not isinstance(value, list):
		return []
	return [item for item in value if isinstance(item, str)]


def _positive_int(value: Any) -> int | None:
	if isinstance(value, bool):
		return None
	try:
		integer = int(value)
	except (TypeError, ValueError):
		return None
	return integer if integer > 0 else None


def _supported_parameter_set(model: dict[str, Any]) -> set[str]:
	return set(_as_string_list(model.get("supported_parameters")))


def _supports_openrouter_effort_levels(model: dict[str, Any]) -> bool:
	return "reasoning" in _supported_parameter_set(model)


def _default_reasoning_parameters(model: dict[str, Any]) -> dict[str, Any]:
	default_parameters = _as_dict(model.get("default_parameters"))
	reasoning_defaults = _as_dict(default_parameters.get("reasoning"))
	if "reasoning_effort" in default_parameters:
		reasoning_defaults.setdefault("effort", default_parameters["reasoning_effort"])
	return reasoning_defaults


def _pricing_hint(model: dict[str, Any]) -> dict[str, Any]:
	pricing = _as_dict(model.get("pricing"))
	return {
		key: pricing[key]
		for key in ("prompt", "completion", "internal_reasoning")
		if pricing.get(key) not in (None, "")
	}


def _price_per_million_tokens(value: Any) -> str | None:
	try:
		price = Decimal(str(value)) * Decimal(1_000_000)
	except (InvalidOperation, ValueError):
		return None

	normalized = price.normalize()
	if normalized == normalized.to_integral():
		return f"${normalized:.0f}/M"
	return f"${normalized:f}/M".rstrip("0").rstrip(".")


def reasoning_cost_hint(capabilities: dict[str, Any]) -> str:
	"""Return a concise UI hint from OpenRouter pricing metadata."""
	if not capabilities.get("supported"):
		return ""

	pricing = _as_dict(capabilities.get("pricing"))
	prompt_price = _price_per_million_tokens(pricing.get("prompt"))
	completion_price = _price_per_million_tokens(pricing.get("completion"))
	internal_reasoning_price = _price_per_million_tokens(pricing.get("internal_reasoning"))

	parts = []
	if prompt_price:
		parts.append(f"input {prompt_price}")
	if completion_price:
		parts.append(f"output {completion_price}")

	if not parts and not internal_reasoning_price:
		return ""

	if internal_reasoning_price:
		reasoning_note = f"; reasoning {internal_reasoning_price}"
	else:
		reasoning_note = ""

	base_hint = "; ".join(parts)
	if base_hint:
		return f"{base_hint}{reasoning_note}"
	return f"{reasoning_note}."


def reasoning_capabilities_for_model(model: dict[str, Any]) -> dict[str, Any]:
	"""Derive serializable reasoning UI capabilities from OpenRouter metadata."""
	supported_parameters = _supported_parameter_set(model)
	default_reasoning = _default_reasoning_parameters(model)
	top_provider = _as_dict(model.get("top_provider"))

	supports_native_effort = bool(
		{"reasoning.effort", "reasoning_effort", "effort"} & supported_parameters
	)
	supports_effort = supports_native_effort or _supports_openrouter_effort_levels(model)
	supports_max_tokens = "reasoning.max_tokens" in supported_parameters or (
		"reasoning" in supported_parameters and "max_tokens" in supported_parameters
	)
	supports_reasoning = (
		supports_effort or supports_max_tokens or "reasoning" in supported_parameters
	)
	max_completion_tokens = _positive_int(top_provider.get("max_completion_tokens"))
	has_token_ceiling = supports_max_tokens and max_completion_tokens is not None

	control_type = "none"
	if supports_effort:
		control_type = "effort"

	default_effort = default_reasoning.get("effort")
	if default_effort not in OPENROUTER_REASONING_EFFORT_CHOICES:
		default_effort = "none" if supports_effort else None

	default_max_tokens = _positive_int(default_reasoning.get("max_tokens"))
	max_reasoning_tokens = (
		int(max_completion_tokens * REASONING_TOKEN_BUDGET_OUTPUT_RATIO)
		if has_token_ceiling
		else None
	)

	if default_max_tokens is not None and max_reasoning_tokens is not None:
		default_max_tokens = min(default_max_tokens, max_reasoning_tokens)
	elif not has_token_ceiling:
		default_max_tokens = None

	return {
		"supported": supports_reasoning,
		"control_type": control_type,
		"supports_effort": supports_effort,
		"effort_choices": list(OPENROUTER_REASONING_EFFORT_CHOICES) if supports_effort else [],
		"default_effort": default_effort,
		"supports_max_tokens": supports_max_tokens,
		"default_max_tokens": default_max_tokens,
		"max_reasoning_tokens": max_reasoning_tokens,
		"pricing": _pricing_hint(model),
	}


def normalize_reasoning_payload(
	model: dict[str, Any],
	settings: dict[str, Any] | None,
) -> dict[str, Any] | None:
	"""Build a supported OpenRouter reasoning payload from user settings."""
	if not isinstance(settings, dict):
		return None

	capabilities = reasoning_capabilities_for_model(model)
	control_type = capabilities["control_type"]
	if control_type != "effort":
		return None

	effort = settings.get("effort")
	if effort not in capabilities["effort_choices"]:
		return None
	if effort == "none":
		return {"effort": "none"}
	return {"effort": effort, "exclude": False}
