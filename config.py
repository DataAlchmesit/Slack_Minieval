"""
config.py
---------
Central configuration for MiniEval for Slack.
All values load from environment variables / .env file.

FIXED: the three SLACK_* lines were indexing os.environ with the literal
placeholder text ("your-slack-bot-token here") instead of the actual env
var name -- guaranteed KeyError on import. Also added
CHANNEL_HISTORY_FALLBACK_LIMIT, which slack_event_handler.py imports but
this file never defined.
"""

from multiprocessing.util import DEBUG
import os
from dotenv import load_dotenv

load_dotenv()


# ── Slack ──────────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN: str = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN: str = os.environ["SLACK_APP_TOKEN"]
SLACK_SIGNING_SECRET: str = os.environ["SLACK_SIGNING_SECRET"]

# ── MCP Server ─────────────────────────────────────────────────────────────────
MCP_HOST: str = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
MCP_PORT: int = int(os.getenv("MCP_SERVER_PORT", "8765"))

# ── NLI Model ──────────────────────────────────────────────────────────────────
NLI_MODEL: str = os.getenv("NLI_MODEL", "cross-encoder/nli-deberta-v3-small")

# ── Thresholds ─────────────────────────────────────────────────────────────────
# Score is a float in [0, 1] representing faithfulness of a summary.
# Below HALLUCINATION_THRESHOLD  → likely hallucinated  → post warning
# Above VERIFIED_THRESHOLD       → faithful              → post verified
# In between                     → uncertain             → silent (no post)
HALLUCINATION_THRESHOLD: float = float(os.getenv("HALLUCINATION_THRESHOLD", "0.40"))
VERIFIED_THRESHOLD: float = float(os.getenv("VERIFIED_THRESHOLD", "0.70"))

# ── Storage ────────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "uploads/minieval.db")

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── Source Content Fallback ─────────────────────────────────────────────────────
# Slack's native "Summarize thread/channel" feature renders privately to the
# user who clicked it -- it's never posted into the channel, so MiniEval is
# invoked on-demand (message shortcut / slash command) rather than via a
# passive listener. When no specific thread is given, we fall back to recent
# channel history as the comparison source.
CHANNEL_HISTORY_FALLBACK_LIMIT: int = int(os.getenv("CHANNEL_HISTORY_FALLBACK_LIMIT", "200"))