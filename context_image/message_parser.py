"""Defensive parsing for private automatic-trigger messages."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True, slots=True)
class TriggerMessage:
    message_id: str
    stream_id: str
    user_id: str
    text: str
    timestamp: float


def is_private_message(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    info = message.get("message_info")
    return isinstance(info, dict) and not bool(info.get("group_info"))


def parse_trigger_message(message: Any, stream_id: str) -> TriggerMessage | None:
    normalized_stream_id = str(stream_id or "").strip()
    if not normalized_stream_id or not is_private_message(message):
        return None
    if message.get("is_command") or message.get("is_notify"):
        return None

    info = message.get("message_info")
    user = info.get("user_info")
    user_id = str(user.get("user_id") or "").strip() if isinstance(user, dict) else ""
    message_id = str(message.get("message_id") or "").strip()
    text = str(message.get("processed_plain_text") or "").strip()
    if not text:
        segments = message.get("raw_message") or message.get("message_segments") or []
        if isinstance(segments, (list, tuple)):
            text = " ".join(
                str(segment.get("data") or "").strip()
                for segment in segments
                if isinstance(segment, dict) and segment.get("type") == "text"
            ).strip()

    timestamp = _parse_timestamp(message.get("timestamp"))
    if not message_id or not user_id or not text:
        return None
    return TriggerMessage(
        message_id,
        normalized_stream_id,
        user_id,
        text,
        timestamp,
    )


def _parse_timestamp(value: Any) -> float:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return timestamp if isfinite(timestamp) else 0.0
