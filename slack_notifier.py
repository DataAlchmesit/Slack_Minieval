"""
slack_notifier.py
-----------------
Builds and posts Block Kit messages back into Slack threads.

Two message types:
  • warning  (⚠️)  — summary likely hallucinated
  • verified (✅)  — summary is faithful
  • canvas_update  — updates the workspace Canvas dashboard
"""

from __future__ import annotations

from typing import Optional, Dict, Any

from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config
from evaluator_bridge import EvalResult

client = WebClient(token=config.SLACK_BOT_TOKEN)


# ── Public Functions ────────────────────────────────────────────────────────────

def post_warning(
    channel_id: str,
    thread_ts: str,
    result: EvalResult,
    summary_preview: str = "",
) -> Optional[str]:
    """
    Post a ⚠️ hallucination warning in the thread.
    Returns the message ts on success, None on failure.
    """
    blocks = _build_warning_blocks(result, summary_preview)
    return _post(channel_id, thread_ts, blocks, text="⚠️ MiniEval: AI summary may contain hallucinations")


def post_verified(
    channel_id: str,
    thread_ts: str,
    result: EvalResult,
) -> Optional[str]:
    """
    Post a ✅ verified confirmation in the thread.
    Returns the message ts on success, None on failure.
    """
    blocks = _build_verified_blocks(result)
    return _post(channel_id, thread_ts, blocks, text="✅ MiniEval: AI summary verified as faithful")


def post_channel_stats(
    channel_id: str,
    stats: dict,
) -> Optional[str]:
    """
    Post a channel stats summary (used by /minieval stats slash command).
    """
    blocks = _build_stats_blocks(stats)
    return _post(channel_id, None, blocks, text="📊 MiniEval Channel Report")


# ── Block Kit Builders ──────────────────────────────────────────────────────────

def _build_warning_blocks(result: EvalResult, summary_preview: str) -> list[dict]:
    score_bar = _score_bar(result.score)
    preview_text = (
        f"_\"{summary_preview[:120]}…\"_" if len(summary_preview) > 120
        else f"_\"{summary_preview}\"_"
    ) if summary_preview else ""

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "⚠️  Heads up — this AI summary may not be accurate",
                "emoji": True,
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*MiniEval* evaluated this Slack AI summary and found "
                    f"low faithfulness to the original thread.\n\n"
                    + (f"{preview_text}\n\n" if preview_text else "")
                    + f"*Faithfulness score:* `{result.score:.0%}`\n"
                    f"{score_bar}"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"🔴 *HALLUCINATED* — score {result.score:.0%} "
                        f"is below the {config.HALLUCINATION_THRESHOLD:.0%} threshold  •  "
                        f"Model: `{_short_model(result.model)}`  •  "
                        f"Latency: {result.latency_ms:.0f}ms"
                        + ("  •  ⚠️ _text truncated_" if result.truncated else "")
                    ),
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Full Thread", "emoji": True},
                    "style": "danger",
                    "action_id": "view_thread",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Re-evaluate", "emoji": True},
                    "action_id": "re_evaluate",
                },
            ],
        },
    ]

    return blocks


def _build_verified_blocks(result: EvalResult) -> list[dict]:
    score_bar = _score_bar(result.score)

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"✅ *MiniEval verified* — this AI summary is faithful "
                    f"to the original thread.\n\n"
                    f"*Faithfulness score:* `{result.score:.0%}`\n"
                    f"{score_bar}"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"🟢 *FAITHFUL* — score {result.score:.0%} "
                        f"exceeds the {config.VERIFIED_THRESHOLD:.0%} threshold  •  "
                        f"Model: `{_short_model(result.model)}`  •  "
                        f"Latency: {result.latency_ms:.0f}ms"
                    ),
                }
            ],
        },
    ]

    return blocks


def _build_stats_blocks(stats: dict) -> list[dict]:
    trust = stats.get("trust_score", 0)
    trust_emoji = "🟢" if trust >= 70 else ("🟡" if trust >= 40 else "🔴")
    hal_rate = stats.get("hallucination_rate_pct", 0)
    total = stats.get("total_evaluations", 0)
    days = stats.get("days", 30)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 MiniEval Channel Report",
                "emoji": True,
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Trust Score*\n{trust_emoji} `{trust}%`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Hallucination Rate*\n🔴 `{hal_rate}%`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Total Evaluations*\n📝 `{total}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Period*\n📅 Last `{days}` days",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Powered by *MiniEval* — NLI-based faithfulness scoring via MCP",
                }
            ],
        },
    ]

    return blocks


# ── Internals ───────────────────────────────────────────────────────────────────

def _post(
    channel_id: str,
    thread_ts: Optional[str],
    blocks: list[dict],
    text: str,
) -> Optional[str]:
    """
    Internal function to post a message to Slack.
    
    Args:
        channel_id: Slack channel ID
        thread_ts: Thread timestamp (optional)
        blocks: Block Kit payload
        text: Fallback text
    
    Returns:
        Message timestamp on success, None on failure
    """
    # Build payload safely
    payload: dict = {
        "channel": channel_id,
        "blocks": blocks,
        "text": text,
    }
    
    if thread_ts:
        payload["thread_ts"] = thread_ts

    try:
        resp = client.chat_postMessage(**payload)
        logger.info(f"Posted message to {channel_id} ts={resp['ts']}")
        return resp["ts"]
    except SlackApiError as exc:
        logger.error(f"Slack API error posting message: {exc.response['error']}")
        return None


def _score_bar(score: float, width: int = 10) -> str:
    """Simple text progress bar: ██████░░░░  60%"""
    filled = round(score * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"`{bar}`"


def _short_model(model_name: str) -> str:
    """Extract short model name from full path."""
    return model_name.split("/")[-1]


# ── Compatibility Class ──────────────────────────────────────────────────────────

class SlackNotifier:
    """
    Compatibility wrapper for the module-level functions.
    
    This class wraps the existing post_warning, post_verified, and
    post_channel_stats functions to provide a consistent interface
    for the pipeline and event handler.
    """
    
    def __init__(self, token: str):
        """
        Initialize the notifier.
        
        Args:
            token: Slack bot token (required for client initialization)
        """
        self.token = token
        logger.info("SlackNotifier initialized")
    
    def post_warning(
        self,
        channel_id: str,
        thread_ts: str,
        score: float,
        details: dict,
        summary_preview: str = ""
    ) -> Optional[str]:
        """
        Post a warning message using the module-level function.
        
        Args:
            channel_id: Slack channel ID
            thread_ts: Thread timestamp
            score: Faithfulness score
            details: Evaluation details dict
            summary_preview: Preview of the summary text
            
        Returns:
            Message timestamp on success, None on failure
        """
        # Build EvalResult from dict
        result = EvalResult(
            score=score,
            label=details.get("label", "HALLUCINATED"),
            entailment_prob=details.get("entailment_prob", 0.0),
            contradiction_prob=details.get("contradiction_prob", 0.0),
            neutral_prob=details.get("neutral_prob", 0.0),
            model=details.get("model", config.NLI_MODEL),
            latency_ms=details.get("latency_ms", 0.0),
            truncated=details.get("truncated", False),
            error=details.get("error"),
            raw_scores=details.get("raw_scores", {})
        )
        
        # Call the module-level function
        return post_warning(channel_id, thread_ts, result, summary_preview)
    
    def post_verified(
        self,
        channel_id: str,
        thread_ts: str,
        score: float,
        details: dict
    ) -> Optional[str]:
        """
        Post a verified message using the module-level function.
        
        Args:
            channel_id: Slack channel ID
            thread_ts: Thread timestamp
            score: Faithfulness score
            details: Evaluation details dict
            
        Returns:
            Message timestamp on success, None on failure
        """
        # Build EvalResult from dict
        result = EvalResult(
            score=score,
            label=details.get("label", "FAITHFUL"),
            entailment_prob=details.get("entailment_prob", 0.0),
            contradiction_prob=details.get("contradiction_prob", 0.0),
            neutral_prob=details.get("neutral_prob", 0.0),
            model=details.get("model", config.NLI_MODEL),
            latency_ms=details.get("latency_ms", 0.0),
            truncated=details.get("truncated", False),
            error=details.get("error"),
            raw_scores=details.get("raw_scores", {})
        )
        
        # Call the module-level function
        return post_verified(channel_id, thread_ts, result)
    
    def post_stats(
        self,
        channel_id: str,
        stats: dict
    ) -> Optional[str]:
        """
        Post channel statistics using the module-level function.
        
        Args:
            channel_id: Slack channel ID
            stats: Statistics dict
            
        Returns:
            Message timestamp on success, None on failure
        """
        return post_channel_stats(channel_id, stats)