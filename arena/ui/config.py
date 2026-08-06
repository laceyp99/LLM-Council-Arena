from pathlib import Path

SITE_URL = "http://localhost:7860"
SITE_NAME = "LLM Council Arena"
PANEL_COUNT = 3
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
DEFAULT_MODEL_IDS = [
	"google/gemini-3.5-flash-lite",
	"openai/gpt-5.6-luna",
	"anthropic/claude-sonnet-5",
]
APP_DIR = Path(__file__).resolve().parents[2]
APP_ICON_PATH = APP_DIR / "app" / "icon.png"
VOTES_FILE = APP_DIR / "votes.json"
LOGS_DIR = APP_DIR / "arena_logs"
SESSION_LOGS_DIR = LOGS_DIR / "sessions"
META_LOG_FILE = LOGS_DIR / "meta.json"
ANONYMOUS_PANEL_LABELS = [f"Response {chr(65 + index)}" for index in range(PANEL_COUNT)]
