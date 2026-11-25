from decimal import Decimal, InvalidOperation
from typing import Any

OPENROUTER_REASONING_EFFORT_CHOICES = ["none", "minimal", "low", "medium", "high", "xhigh"]


def _as_dict(value: Any) -> dict[str, Any]:
	return dict(value) if isinstance(value, dict) else {}


def _as_string_list(value: Any) -> list[str]:
	if not isinstance(value, list):
		return []
	return [item for item in value if isinstance(item, str)]


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

	# normalize() already drops trailing zeros, so 0.50 renders as 0.5.
	normalized = price.normalize()
	if normalized == normalized.to_integral():
		return f"${normalized:.0f}/M"
	return f"${normalized:f}/M"


def reasoning_cost_hint(capabilities: dict[str, Any]) -> str:
	"""Return a concise UI hint from OpenRouter pricing metadata."""
	if not capabilities.get("supported"):
		return ""

	pricing = _as_dict(capabilities.get("pricing"))
	parts = []
	for label, key in (
		("input", "prompt"),
		("output", "completion"),
		("reasoning", "internal_reasoning"),
	):
		price = _price_per_million_tokens(pricing.get(key))
		if price:
			parts.append(f"{label} {price}")
	return "; ".join(parts)


def reasoning_capabilities_for_model(model: dict[str, Any]) -> dict[str, Any]:
	"""Derive serializable reasoning UI capabilities from OpenRouter metadata."""
	supported_parameters = _supported_parameter_set(model)
	default_reasoning = _default_reasoning_parameters(model)

	supports_native_effort = bool(
		{"reasoning.effort", "reasoning_effort", "effort"} & supported_parameters
	)
	supports_effort = supports_native_effort or _supports_openrouter_effort_levels(model)
	# Token-budget-only models still count as reasoning models (for the cost hint),
	# even though the UI only offers an effort control.
	supports_reasoning = supports_effort or bool(
		{"reasoning", "reasoning.max_tokens"} & supported_parameters
	)

	control_type = "none"
	if supports_effort:
		control_type = "effort"

	default_effort = default_reasoning.get("effort")
	if default_effort not in OPENROUTER_REASONING_EFFORT_CHOICES:
		default_effort = "medium" if supports_effort else None

	return {
		"supported": supports_reasoning,
		"control_type": control_type,
		"supports_effort": supports_effort,
		"effort_choices": list(OPENROUTER_REASONING_EFFORT_CHOICES) if supports_effort else [],
		"default_effort": default_effort,
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
