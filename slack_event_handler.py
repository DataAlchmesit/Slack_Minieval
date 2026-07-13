"""
slack_event_handler.py
-----------------------
The agent's entry points -- ON-DEMAND triggers, not a passive listener.

Message shortcut ("Check this summary with MiniEval") and slash command
(/minieval-check) both open a modal; the modal submission fetches the
source, evaluates via the pipeline, and posts the result.

SOURCE-FETCH FIX included: _fetch_source_text no longer vacuums 200
messages of unrelated channel history into the "source" (which made
faithful summaries score 0% because they weren't entailed by the noise).
It now filters bot messages, join notices, @-mention lines, and trivial
messages, and when no specific thread is given it uses only a small
recent slice of human messages. The intended primary path is to anchor
to a specific thread (right-click a message, or paste its permalink).
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from slack_bolt import App

import config
from storage import EvaluationStorage
from pipeline import MiniEvalPipeline
from slack_utils import parse_permalink
from modals import build_check_modal, parse_submission, CALLBACK_ID

logger = logging.getLogger(__name__)


class SlackEventHandler:
    """Registers and handles all Slack entry points for MiniEval."""

    def __init__(self, app: App, pipeline: MiniEvalPipeline, storage: EvaluationStorage):
        self.app = app
        self.pipeline = pipeline
        self.storage = storage
        self._register_handlers()
        logger.info("SlackEventHandler initialized with registered handlers")

    # -- Registration -------------------------------------------------------------

    def _register_handlers(self) -> None:
        self.app.shortcut("check_summary_shortcut")(self.handle_check_shortcut)
        self.app.command("/minieval-check")(self.handle_check_command)
        self.app.view(CALLBACK_ID)(self.handle_check_submission)
        self.app.command("/minieval-stats")(self.handle_stats_command)
        self.app.action("view_thread")(self.handle_view_thread)
        self.app.action("re_evaluate")(self.handle_re_evaluate)

    # -- Source Content Retrieval -------------------------------------------------

    def _fetch_source_text(self, channel_id: str, thread_ts: Optional[str]) -> str:
        """
        Pull the original messages a summary is supposed to represent.

        Preferred path: a specific thread_ts (from a pasted permalink or a
        clicked thread reply) -> compare only against THAT thread.

        Fallback path (no thread given): use only the most recent handful
        of *human* messages, not the whole channel. Comparing a summary
        against 200 messages of unrelated history produces meaningless
        low scores -- the summary isn't entailed by noise it has nothing
        to do with.
        """
        try:
            if thread_ts:
                resp = self.app.client.conversations_replies(
                    channel=channel_id,
                    ts=thread_ts,
                    limit=config.CHANNEL_HISTORY_FALLBACK_LIMIT,
                )
                messages = resp.get("messages", [])
            else:
                resp = self.app.client.conversations_history(
                    channel=channel_id,
                    limit=config.CHANNEL_HISTORY_FALLBACK_LIMIT,
                )
                messages = resp.get("messages", [])

            clean = [self._clean_message_text(m) for m in messages]
            clean = [c for c in clean if c]

            # Fallback (no specific thread): keep only the most recent
            # few human messages so we compare against a focused slice,
            # not the entire channel.
            if not thread_ts:
                clean = list(reversed(clean))[:15]
                clean = list(reversed(clean))

            return "\n".join(clean)

        except Exception as exc:
            logger.error(f"Failed to fetch source text for {channel_id}: {exc}")
            return ""

    def _clean_message_text(self, m: dict) -> str:
        """
        Return usable human source text from a message, or "" to skip it.
        Skips bot messages, channel_join/leave subtypes, @-mention-only
        lines, and trivially short messages.
        """
        if m.get("bot_id"):
            return ""
        if m.get("subtype"):  # channel_join, channel_leave, etc.
            return ""

        text = (m.get("text") or "").strip()
        if not text:
            return ""

        stripped = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if len(stripped) < 15:
            return ""
        if stripped.lower() in ("hello", "test", "hi", "hey"):
            return ""

        return stripped

    # -- Entry Point 1: Message Shortcut ------------------------------------------

    def handle_check_shortcut(self, ack, shortcut, client):
        ack()

        message = shortcut.get("message", {})
        channel_id = shortcut.get("channel", {}).get("id", "")
        thread_ts = message.get("thread_ts", "") or message.get("ts", "")

        view = build_check_modal(
            prefill_summary=message.get("text", ""),
            prefill_channel=channel_id,
            prefill_permalink="",
            private_metadata=f"{shortcut['user']['id']}|{thread_ts}",
        )

        client.views_open(trigger_id=shortcut["trigger_id"], view=view)

    # -- Entry Point 2: Slash Command ---------------------------------------------

    def handle_check_command(self, ack, command, client):
        ack()

        view = build_check_modal(
            prefill_channel=command.get("channel_id", ""),
            private_metadata=f"{command['user_id']}|",
        )

        client.views_open(trigger_id=command["trigger_id"], view=view)

    # -- Modal Submission (shared by both entry points) ----------------------------

    def handle_check_submission(self, ack, body, view, client):
        ack()

        fields = parse_submission(view)
        requesting_user, fallback_thread_ts = (view["private_metadata"] or "|").split("|", 1)

        channel_id = fields["source_channel"]
        summary_text = fields["summary_text"].strip()
        permalink = fields["thread_permalink"].strip()

        thread_ts = fallback_thread_ts or None
        if permalink:
            parsed = parse_permalink(permalink)
            if parsed:
                channel_id, thread_ts = parsed
            else:
                logger.warning(f"Could not parse permalink, ignoring: {permalink}")

        def _worker():
            source_text = self._fetch_source_text(channel_id, thread_ts)

            if not source_text:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=requesting_user,
                    text=(
                        "Could not find any source messages to compare against "
                        "in that channel/thread -- nothing was evaluated."
                    ),
                )
                return

            result = self.pipeline.evaluate(
                summary=summary_text,
                context=source_text,
                channel=channel_id,
                thread_ts=thread_ts or "",
            )

            def _fallback_say(text):
                client.chat_postEphemeral(channel=channel_id, user=requesting_user, text=text)

            self.pipeline.notify_result(say=_fallback_say, result=result, thread_ts=thread_ts or "")

            label = result.get("label")
            if label in ("FAITHFUL", "HALLUCINATED"):
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=requesting_user,
                    text=f"MiniEval finished -- result posted in the channel (score: {result.get('score', 0):.0%}).",
                )
            elif label == "UNCERTAIN":
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=requesting_user,
                    text=(
                        f"MiniEval finished -- score {result.get('score', 0):.0%} was "
                        f"inconclusive (between thresholds), so nothing was posted in-channel."
                    ),
                )
            else:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=requesting_user,
                    text="MiniEval ran into an error during evaluation. Check the logs.",
                )

        threading.Thread(target=_worker, daemon=True).start()

    # -- Slash Command: /minieval-stats ---------------------------------------------

    def handle_stats_command(self, ack, body):
        ack()
        channel_id = body.get("channel_id")
        stats = self.storage.get_channel_stats(channel_id, days=30)
        self.pipeline.notifier.post_stats(channel_id, stats)

    # -- Button Actions ---------------------------------------------------------------

    def handle_view_thread(self, ack):
        ack()

    def handle_re_evaluate(self, ack, respond):
        ack()
        respond(
            text=(
                "To re-run this check, use the *Check this summary with MiniEval* "
                "shortcut on the message again -- re-evaluation needs the original "
                "summary text, which isn't stored on this button."
            ),
            replace_original=False,
        )