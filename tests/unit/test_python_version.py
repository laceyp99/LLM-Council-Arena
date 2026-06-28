import pytest

import arena


def test_supported_python_version_passes(monkeypatch) -> None:
	monkeypatch.setattr(arena.sys, "version_info", (3, 13, 0))

	arena._ensure_supported_python()


def test_unsupported_python_version_fails_with_clear_message(monkeypatch) -> None:
	monkeypatch.setattr(arena.sys, "version_info", (3, 12, 0))

	with pytest.raises(RuntimeError) as exc_info:
		arena._ensure_supported_python()

	message = str(exc_info.value)
	assert "requires Python 3.13 or newer" in message
	assert "Current interpreter is Python 3.12.0" in message
