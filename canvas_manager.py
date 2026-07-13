"""
canvas_manager.py
------------------
Creates and updates a Slack Canvas dashboard for MiniEval.

The Canvas is attached directly to the channel as a tab — visible to
every team member without them having to run a command. It updates
automatically every time an evaluation runs, so the trust score is
always current.

Content layout:
  - Workspace Trust Score (headline number)
  - This week's evaluation volume + hallucination rate
  - Per-channel breakdown table
  - How to use MiniEval
  - Last updated timestamp

Usage from other modules:
    from canvas_manager import refresh_canvas
    refresh_canvas(client, channel_id, storage)

The Canvas ID is persisted in SQLite so the same canvas gets updated
rather than a new one created on every evaluation.
"""

from __future__ import annotations

import sqlite3
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config


# ── Canvas ID persistence ──────────────────────────────────────────────────────
# Store canvas IDs in a tiny separate table so we update the same canvas
# rather than flooding the channel with new ones.

def _get_db_conn() -> sqlite3.Connection:
    db_path = config.DB_PATH
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS canvas_registry (
            channel_id  TEXT PRIMARY KEY,
            canvas_id   TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _get_canvas_id(channel_id: str) -> Optional[str]:
    with _get_db_conn() as conn:
        row = conn.execute(
            "SELECT canvas_id FROM canvas_registry WHERE channel_id = ?",
            (channel_id,)
        ).fetchone()
    return row[0] if row else None


def _save_canvas_id(channel_id: str, canvas_id: str) -> None:
    with _get_db_conn() as conn:
        conn.execute("""
            INSERT INTO canvas_registry (channel_id, canvas_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET canvas_id = excluded.canvas_id
        """, (channel_id, canvas_id, datetime.utcnow().isoformat()))
        conn.commit()


# ── Content Builder ────────────────────────────────────────────────────────────

def _build_canvas_markdown(workspace_stats: dict, channel_stats: dict) -> str:
    """
    Build the full canvas content as Slack markdown.
    Slack Canvas supports a subset of markdown plus emoji.
    """
    trust = workspace_stats.get("workspace_trust_score", 100.0)
    total = workspace_stats.get("total_evaluations", 0)
    hal_rate = workspace_stats.get("hallucination_rate_pct", 0.0)
    days = workspace_stats.get("days", 7)

    # Trust score emoji and label
    if trust >= 50:
        trust_emoji = "🟢"
        trust_label = "Good"
    elif trust >= 20:
        trust_emoji = "🟡"
        trust_label = "Needs attention"
    else:
        trust_emoji = "🔴"
        trust_label = "High risk"

    now = datetime.utcnow().strftime("%b %d, %Y at %H:%M UTC")

    lines = [
        "# 🛡️ MiniEval — AI Trust Dashboard",
        "",
        f"> **Workspace AI Trust Score: {trust:.0f}%** {trust_emoji} {trust_label}",
        "",
        "---",
        "",
        "## 📊 This Week at a Glance",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total evaluations | **{total}** |",
        f"| Hallucination rate | **{hal_rate:.1f}%** |",
        f"| Faithful summaries | **{100 - hal_rate:.1f}%** |",
        f"| Period | Last {days} days |",
        "",
        "---",
        "",
        "## 📋 Channel Breakdown",
        "",
    ]

    channels = workspace_stats.get("channels", [])
    if channels:
        lines += [
            "| Channel | Evaluations | Hallucination Rate | Trust Score |",
            "|---------|-------------|-------------------|-------------|",
        ]
        for ch in channels:
            ch_trust = ch.get("trust_score", 100)
            ch_hal = ch.get("hallucination_rate_pct", 0)
            ch_emoji = "🟢" if ch_trust >= 50 else ("🟡" if ch_trust >= 20 else "🔴")
            ch_id = ch.get("channel_id", "")
            lines.append(
                f"| <#{ch_id}> | {ch.get('evaluations', 0)} | {ch_hal:.1f}% | {ch_emoji} {ch_trust:.0f}% |"
            )
    else:
        lines.append("_No channel data yet. Run `/minieval-check` in any channel to start._")

    lines += [
        "",
        "---",
        "",
        "## 🚀 How to Use MiniEval",
        "",
        "**Check any AI summary in 3 steps:**",
        "",
        "1. Right-click any message containing a summary",
        "2. Select **'Check this summary with MiniEval'** from the menu",
        "3. Confirm the channel and hit **Evaluate**",
        "",
        "**Or use the slash command:**",
        "```",
        "/minieval-check",
        "```",
        "",
        "**Check your channel's trust score:**",
        "```",
        "/minieval-stats",
        "```",
        "",
        "---",
        "",
        "## 🧠 How It Works",
        "",
        "MiniEval uses NLI (Natural Language Inference) to score how faithfully",
        "an AI summary represents its source content.",
        "",
        "| Score | Verdict | Action |",
        "|-------|---------|--------|",
        "| 50%+ | ✅ Faithful | Verified card posted |",
        "| 20–50% | ⚪ Uncertain | No action (inconclusive) |",
        "| Under 20% | ⚠️ Hallucinated | Warning posted in thread |",
        "",
        "The evaluation engine runs locally — **no message content ever leaves your workspace.**",
        "",
        "---",
        "",
        f"_Last updated: {now}_",
        "_Powered by MiniEval · NLI-based faithfulness scoring via MCP_",
    ]

    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

def refresh_canvas(
    client: WebClient,
    channel_id: str,
    storage,  # EvaluationStorage instance
) -> Optional[str]:
    """
    Create (first time) or update (subsequent times) the MiniEval Canvas
    for a channel. Returns the canvas_id on success, None on failure.

    Call this after every evaluation that posts to the channel, so the
    dashboard always reflects the current state.
    """
    from storage import EvaluationStorage
    if not isinstance(storage, EvaluationStorage):
        logger.warning("refresh_canvas: storage is not an EvaluationStorage instance.")
        return None

    # Build stats
    channel_stats = storage.get_channel_stats(channel_id, days=30)
    workspace_stats = _build_workspace_stats(storage, days=7)
    markdown = _build_canvas_markdown(workspace_stats, channel_stats)

    canvas_id = _get_canvas_id(channel_id)

    if canvas_id:
        return _update_canvas(client, canvas_id, markdown, channel_id)
    else:
        return _create_canvas(client, channel_id, markdown)


def _build_workspace_stats(storage, days: int = 7) -> dict:
    """Aggregate stats across all channels from storage."""
    import sqlite3
    from datetime import timedelta
    from collections import defaultdict

    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    try:
        with sqlite3.connect(storage.db_path) as conn:
            rows = conn.execute(
                """
                SELECT channel, score, is_hallucination
                FROM evaluations
                WHERE timestamp >= ?
                """,
                (since,)
            ).fetchall()
    except Exception as e:
        logger.error(f"Failed to fetch workspace stats: {e}")
        return {"total_evaluations": 0, "workspace_trust_score": 100.0, "channels": [], "days": days}

    total = len(rows)
    if total == 0:
        return {"total_evaluations": 0, "workspace_trust_score": 100.0, "hallucination_rate_pct": 0.0, "channels": [], "days": days}

    hallucinated = sum(1 for r in rows if r[2])
    channel_data = defaultdict(list)
    for r in rows:
        channel_data[r[0]].append(r)

    channels = []
    for ch_id, ch_rows in channel_data.items():
        ch_hal = sum(1 for r in ch_rows if r[2])
        channels.append({
            "channel_id": ch_id,
            "evaluations": len(ch_rows),
            "hallucination_rate_pct": round(ch_hal / len(ch_rows) * 100, 1),
            "trust_score": round((1 - ch_hal / len(ch_rows)) * 100, 1),
        })

    channels.sort(key=lambda x: x["hallucination_rate_pct"], reverse=True)

    return {
        "days": days,
        "total_evaluations": total,
        "hallucination_rate_pct": round(hallucinated / total * 100, 1),
        "workspace_trust_score": round((1 - hallucinated / total) * 100, 1),
        "channels": channels,
    }


def _create_canvas(client: WebClient, channel_id: str, markdown: str) -> Optional[str]:
    """Create a new canvas attached to the channel."""
    try:
        resp = client.api_call(
            "conversations.canvases.create",
            json={
                "channel_id": channel_id,
                "document_content": {
                    "type": "markdown",
                    "markdown": markdown,
                },
            },
        )
        canvas_id = resp["canvas_id"]
        _save_canvas_id(channel_id, canvas_id)
        logger.info(f"Canvas created: {canvas_id} for channel {channel_id}")
        return canvas_id
    except SlackApiError as e:
        logger.error(f"Failed to create canvas: {e.response.get('error', e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error creating canvas: {e}")
        return None


def _update_canvas(
    client: WebClient, canvas_id: str, markdown: str, channel_id: str
) -> Optional[str]:
    """Update an existing canvas with fresh content."""
    try:
        # To update a canvas we need to replace its content.
        # The Slack Canvas edit API requires finding existing section IDs
        # and replacing them. The simplest reliable approach for a
        # dashboard that gets fully rewritten on every update is to
        # delete all sections and recreate them.
        # For the hackathon demo, we use a single full-document replace.
        client.api_call(
            "canvases.edit",
            json={
                "canvas_id": canvas_id,
                "changes": [
                    {
                        "operation": "replace",
                        "document_content": {
                            "type": "markdown",
                            "markdown": markdown,
                        },
                    }
                ],
            },
        )
        logger.info(f"Canvas updated: {canvas_id}")
        return canvas_id
    except SlackApiError as e:
        error = e.response.get("error", str(e))
        logger.error(f"Failed to update canvas {canvas_id}: {error}")
        if error in ("canvas_not_found", "not_allowed_token_type"):
            # Canvas was deleted or token scope changed -- recreate
            logger.info("Attempting to recreate canvas...")
            _save_canvas_id(channel_id, "")
            return None
        return None
    except Exception as e:
        logger.error(f"Unexpected error updating canvas: {e}")
        return None