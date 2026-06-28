import sys

MINIMUM_SUPPORTED_PYTHON = (3, 13)


def _ensure_supported_python() -> None:
	if sys.version_info < MINIMUM_SUPPORTED_PYTHON:
		required_version = ".".join(str(part) for part in MINIMUM_SUPPORTED_PYTHON)
		current_version = ".".join(str(part) for part in sys.version_info[:3])
		raise RuntimeError(
			f"LLM Council Arena requires Python {required_version} or newer. "
			f"Current interpreter is Python {current_version}. "
			"Create or activate a Python 3.13 environment before running local commands."
		)


_ensure_supported_python()
