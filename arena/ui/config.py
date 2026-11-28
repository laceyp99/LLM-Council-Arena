from pathlib import Path

SITE_URL = "http://localhost:7860"
SITE_NAME = "LLM Council Arena"
PANEL_COUNT = 3
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
DEFAULT_MODEL_IDS = [
	"openai/gpt-6.1-terra",
	"anthropic/claude-sonnet-5.5",
	"google/gemini-4-hydrogen",
]
APP_ICON_PATH = Path(__file__).resolve().parents[1] / "assets" / "icon.png"
# Runtime data and .env live in the folder the app is started from, not next to the code,
# so a regular (non-editable) install never writes into site-packages.
DATA_DIR = Path.cwd()
ENV_FILE = DATA_DIR / ".env"
VOTES_FILE = DATA_DIR / "votes.jsonl"
LOGS_DIR = DATA_DIR / "arena_logs"
SESSION_LOGS_DIR = LOGS_DIR / "sessions"
META_LOG_FILE = LOGS_DIR / "meta.json"
ANONYMOUS_PANEL_LABELS = [f"Response {chr(65 + index)}" for index in range(PANEL_COUNT)]
