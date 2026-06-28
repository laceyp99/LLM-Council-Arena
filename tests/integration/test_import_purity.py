import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_importing_arena_app_does_not_create_persistence_files(tmp_path: Path) -> None:
	repo_root = Path(__file__).resolve().parents[2]
	temp_root = tmp_path / "import-root"
	temp_root.mkdir()

	script = textwrap.dedent(
		"""
		import importlib
		import sys
		import types
		from pathlib import Path

		temp_root = Path(sys.argv[1])

		config_module = types.ModuleType("arena.ui.config")
		config_module.APP_DIR = temp_root
		config_module.DEFAULT_MODEL_IDS = [
			"openai/gpt-5.4-mini",
			"anthropic/claude-sonnet-4.5",
			"google/gemini-3.1-flash-lite-preview",
		]
		config_module.DEFAULT_REASONING_SETTINGS = {"enabled": True}
		config_module.DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
		config_module.ANONYMOUS_PANEL_LABELS = ["Response A", "Response B", "Response C"]
		config_module.LOGS_DIR = temp_root / "arena_logs"
		config_module.META_LOG_FILE = config_module.LOGS_DIR / "meta.json"
		config_module.PANEL_COUNT = 3
		config_module.SESSION_LOGS_DIR = config_module.LOGS_DIR / "sessions"
		config_module.SITE_NAME = "LLM Council Arena"
		config_module.SITE_URL = "http://localhost:7860"
		config_module.VOTES_FILE = temp_root / "votes.json"
		sys.modules["arena.ui.config"] = config_module

		app_module = importlib.import_module("arena.app")

		assert not config_module.VOTES_FILE.exists()
		assert not config_module.LOGS_DIR.exists()
		assert app_module.demo is None
		"""
	)

	result = subprocess.run(
		[sys.executable, "-c", script, str(temp_root)],
		cwd=repo_root,
		capture_output=True,
		text=True,
		env={**os.environ, "OPENROUTER_API_KEY": ""},
	)

	assert result.returncode == 0, result.stderr


def test_importing_arena_app_does_not_load_openrouter_catalog() -> None:
	repo_root = Path(__file__).resolve().parents[2]
	script = textwrap.dedent(
		"""
		import importlib

		from arena.core import models as model_module

		def fail_load_dotenv():
			raise AssertionError("load_dotenv should not run during arena.app import")

		class ExplodingOpenRouterAPI:
			def __init__(self, *args, **kwargs) -> None:
				raise AssertionError("OpenRouterAPI should not be constructed during arena.app import")

		model_module.load_dotenv = fail_load_dotenv
		model_module.OpenRouterAPI = ExplodingOpenRouterAPI

		app_module = importlib.import_module("arena.app")

		assert app_module.OPENROUTER_API_KEY is None
		assert app_module.demo is None
		assert "not been initialized" in app_module.MODEL_CATALOG_STATUS
		"""
	)

	result = subprocess.run(
		[sys.executable, "-c", script],
		cwd=repo_root,
		capture_output=True,
		text=True,
		env={**os.environ, "OPENROUTER_API_KEY": "test-api-key"},
	)

	assert result.returncode == 0, result.stderr
