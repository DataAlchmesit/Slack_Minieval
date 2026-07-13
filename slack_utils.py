"""
slack_utils.py
---------------
Small helpers shared by the trigger handlers and modal builders.
"""

from __future__ import annotations

import re
from typing import Optional


_PERMALINK_RE = re.compile(
    r"archives/(?P<channel>[A-Z0-9]+)/p(?P<ts_digits>\d+)"
)


def parse_permalink(text: str) -> Optional[tuple[str, str]]:
    """
    Extract (channel_id, thread_ts) from a pasted Slack permalink, e.g.:
        https://yourteam.slack.com/archives/C0123ABCD/p1718000000123456
    Returns None if `text` doesn't contain a recognizable permalink.

    Slack permalink timestamps are the message ts with the decimal point
    removed (10 digits seconds + 6 digits microseconds). We reinsert the
    decimal point to get back the canonical "1718000000.123456" ts format.
    """
    match = _PERMALINK_RE.search(text)
    if not match:
        return None

    channel_id = match.group("channel")
    digits = match.group("ts_digits")

    if len(digits) <= 6:
        return None  # malformed, shouldn't happen with real Slack links

    ts = f"{digits[:-6]}.{digits[-6:]}"
    return channel_id, ts


def truncate_for_modal(text: str, limit: int = 2900) -> str:
    """
    Slack plain_text_input blocks cap initial_value around 3000 chars.
    Trim with a marker so we never hit a 'value too long' modal error.
    """
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"