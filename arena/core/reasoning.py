from typing import Any

OPENROUTER_REASONING_EFFORT_CHOICES = ["none", "low", "medium", "high"]
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


def reasoning_capabilities_for_model(model: dict[str, Any]) -> dict[str, Any]:
	"""Derive serializable reasoning UI capabilities from OpenRouter metadata."""
	supported_parameters = _supported_parameter_set(model)
	default_reasoning = _default_reasoning_parameters(model)
	top_provider = _as_dict(model.get("top_provider"))

	supports_effort = bool({"reasoning.effort", "reasoning_effort"} & supported_parameters)
	supports_max_tokens = "reasoning.max_tokens" in supported_parameters
	supports_reasoning = (
		supports_effort or supports_max_tokens or "reasoning" in supported_parameters
	)
	max_completion_tokens = _positive_int(top_provider.get("max_completion_tokens"))
	has_budget_slider = supports_max_tokens and max_completion_tokens is not None

	control_type = "none"
	if supports_effort:
		control_type = "effort"
	elif has_budget_slider:
		control_type = "budget"
	elif supports_reasoning:
		control_type = "toggle"

	default_effort = default_reasoning.get("effort")
	if default_effort not in OPENROUTER_REASONING_EFFORT_CHOICES:
		default_effort = "medium" if supports_effort else None

	default_max_tokens = _positive_int(default_reasoning.get("max_tokens"))
	max_reasoning_tokens = (
		int(max_completion_tokens * REASONING_TOKEN_BUDGET_OUTPUT_RATIO)
		if has_budget_slider
		else None
	)

	if default_max_tokens is not None and max_reasoning_tokens is not None:
		default_max_tokens = min(default_max_tokens, max_reasoning_tokens)
	elif not has_budget_slider:
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
