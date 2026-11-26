import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace


def _startup_selector_config(
	*,
	catalog_status: str,
	api_key: str | None,
	ready: bool,
) -> dict[str, object]:
	repo_root = Path(__file__).resolve().parents[2]
	script = textwrap.dedent(
		"""
		import importlib
		import json
		import sys

		import arena.core.models as model_module

		model_module.load_model_catalog = lambda site_url, site_name: model_module.CatalogState(
			[
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
					"model_id": "gamma/three",
					"provider_key": "gamma",
					"provider_label": "Gamma",
					"model_label": "Three",
					"full_label": "Gamma: Three",
				},
			],
			sys.argv[1],
			None if sys.argv[2] == "__none__" else sys.argv[2],
			sys.argv[3] == "ready",
		)

		app_module = importlib.import_module("arena.app")
		app_module.initialize_model_catalog()
		arena_demo = app_module.create_demo()
		banner_config = arena_demo.status_banner.get_config()
		print(
			json.dumps(
				{
					"status": app_module.CATALOG.status,
					"provider_interactive": arena_demo.panel_providers[0].get_config().get("interactive"),
					"model_interactive": arena_demo.panel_models[0].get_config().get("interactive"),
					"send_interactive": arena_demo.send_button.get_config().get("interactive"),
					"readiness_banner_visible": banner_config.get("visible"),
					"readiness_banner_value": banner_config.get("value"),
				},
				default=str,
			)
		)
		"""
	)

	result = subprocess.run(
		[
			sys.executable,
			"-c",
			script,
			catalog_status,
			api_key if api_key is not None else "__none__",
			"ready" if ready else "not-ready",
		],
		cwd=repo_root,
		capture_output=True,
		text=True,
		env={**os.environ, "OPENROUTER_API_KEY": ""},
	)

	assert result.returncode == 0, result.stderr
	return json.loads(result.stdout)


def test_package_imports() -> None:
	arena_package = importlib.import_module("arena")
	arena_app = importlib.import_module("arena.app")

	assert arena_package is not None
	assert arena_app is not None


def test_main_entrypoint_launches_demo(monkeypatch) -> None:
	main_module = importlib.import_module("arena.__main__")
	launch_calls: list[str] = []

	def fake_bootstrap_persistence() -> None:
		launch_calls.append("bootstrap")

	def fake_initialize_model_catalog() -> None:
		launch_calls.append("initialize")

	class FakeQueuedDemo:
		def launch(self, **kwargs: object) -> None:
			assert kwargs["favicon_path"] == main_module.APP_ICON_PATH
			launch_calls.append("launch")

	class FakeBlocks:
		def queue(self) -> FakeQueuedDemo:
			launch_calls.append("queue")
			return FakeQueuedDemo()

	monkeypatch.setattr(main_module, "bootstrap_persistence", fake_bootstrap_persistence)
	monkeypatch.setattr(main_module, "initialize_model_catalog", fake_initialize_model_catalog)
	monkeypatch.setattr(main_module, "create_demo", lambda: SimpleNamespace(blocks=FakeBlocks()))

	main_module.main()

	assert launch_calls == ["bootstrap", "initialize", "queue", "launch"]


def test_startup_keeps_model_selectors_active_when_live_catalog_loads() -> None:
	selector_config = _startup_selector_config(
		catalog_status="Loaded 3 text-capable models from OpenRouter.",
		api_key="test-api-key",
		ready=True,
	)

	assert "loaded 3 text-capable models" in str(selector_config["status"]).lower()
	assert selector_config["provider_interactive"] is not False
	assert selector_config["model_interactive"] is not False
	assert selector_config["send_interactive"] is not False
	assert selector_config["readiness_banner_visible"] is False
	assert selector_config["readiness_banner_value"] == ""


def test_startup_disables_model_selectors_when_api_key_validation_fails() -> None:
	selector_config = _startup_selector_config(
		catalog_status=(
			"Warning: could not validate OPENROUTER_API_KEY (unauthorized). "
			"Using the fallback model list."
		),
		api_key="test-api-key",
		ready=False,
	)

	assert "warning" in str(selector_config["status"]).lower()
	assert "validate openrouter_api_key" in str(selector_config["status"]).lower()
	assert selector_config["provider_interactive"] is False
	assert selector_config["model_interactive"] is False
	assert selector_config["send_interactive"] is False
	assert selector_config["readiness_banner_visible"] is True
	assert "OpenRouter is not ready" in str(selector_config["readiness_banner_value"])
	assert "OPENROUTER_API_KEY" in str(selector_config["readiness_banner_value"])
