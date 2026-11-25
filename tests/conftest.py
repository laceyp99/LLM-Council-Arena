from collections.abc import Callable
from typing import Any

import pytest

from arena import app as app_module
from arena.core.models import CatalogState


@pytest.fixture
def anyio_backend():
	return "asyncio"


@pytest.fixture
def use_catalog(monkeypatch: pytest.MonkeyPatch) -> Callable[..., CatalogState]:
	"""Swap the app catalog for one built from test entries; missing labels derive from model_id."""

	def install(
		*entries: dict[str, Any],
		api_key: str | None = "test-api-key",
		ready: bool = True,
	) -> CatalogState:
		catalog = []
		for entry in entries:
			model_id = entry["model_id"]
			provider_key = model_id.split("/", 1)[0]
			catalog.append(
				{
					"provider_key": provider_key,
					"provider_label": provider_key.title(),
					"model_label": model_id,
					"full_label": model_id,
					**entry,
				}
			)
		state = CatalogState(catalog, "Test catalog.", api_key, ready)
		monkeypatch.setattr(app_module, "CATALOG", state)
		return state

	return install
