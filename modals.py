"""
modals.py
---------
Builds the Block Kit modal view used by both trigger entry points
(message shortcut and slash command) to collect the summary text
and the source thread/channel to check it against.
"""

from __future__ import annotations

from slack_utils import truncate_for_modal


CALLBACK_ID = "minieval_check_modal"


def build_check_modal(
    prefill_summary: str = "",
    prefill_channel: str = "",
    prefill_permalink: str = "",
    private_metadata: str = "",
) -> dict:
    """
    Build the modal view payload.

    Parameters
    ----------
    prefill_summary   : Pre-fills the summary text box (from a message shortcut).
    prefill_channel   : Default-selected channel in the conversations_select.
    prefill_permalink : Pre-fills the thread reference field, if known.
    private_metadata  : Passed through untouched — used to carry the
                         triggering user_id between the shortcut/command
                         call and the view_submission handler.
    """
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Paste the AI-generated summary you want checked, "
                    "and tell MiniEval where the *original* conversation "
                    "lives. It'll score how faithful the summary is and "
                    "post the result."
                ),
            },
        },
        {
            "type": "input",
            "block_id": "summary_block",
            "label": {"type": "plain_text", "text": "Summary text to check"},
            "element": {
                "type": "plain_text_input",
                "action_id": "summary_text",
                "multiline": True,
                "initial_value": truncate_for_modal(prefill_summary),
                "placeholder": {
                    "type": "plain_text",
                    "text": "Paste the summary Slack AI (or anything else) generated…",
                },
            },
        },
        {
            "type": "input",
            "block_id": "channel_block",
            "label": {"type": "plain_text", "text": "Channel the summary is about"},
            "element": {
                "type": "conversations_select",
                "action_id": "source_channel",
                **(
                    {"initial_conversation": prefill_channel}
                    if prefill_channel else {}
                ),
                "filter": {
                    "include": ["public", "private"],
                    "exclude_bot_users": True,
                },
            },
        },
        {
            "type": "input",
            "block_id": "permalink_block",
            "optional": True,
            "label": {
                "type": "plain_text",
                "text": "Specific thread (optional)",
            },
            "element": {
                "type": "plain_text_input",
                "action_id": "thread_permalink",
                "initial_value": prefill_permalink,
                "placeholder": {
                    "type": "plain_text",
                    "text": "Paste a message link, or leave blank to use recent channel history",
                },
            },
            "hint": {
                "type": "plain_text",
                "text": "Right-click any message → Copy link, then paste it here.",
            },
        },
    ]

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "MiniEval Check"},
        "submit": {"type": "plain_text", "text": "Evaluate"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def parse_submission(view: dict) -> dict:
    """Extract field values from a view_submission payload."""
    values = view["state"]["values"]
    return {
        "summary_text": values["summary_block"]["summary_text"]["value"] or "",
        "source_channel": values["channel_block"]["source_channel"]["selected_conversation"],
        "thread_permalink": (
            values["permalink_block"]["thread_permalink"]["value"] or ""
        ),
    }