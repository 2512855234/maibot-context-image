"""Observe a later Bot reply before automatic image delivery."""

from __future__ import annotations

import asyncio
from math import isfinite
from time import monotonic
from typing import Any, Awaitable, Callable


class ReplyGate:
    def __init__(
        self,
        message_proxy: Any,
        *,
        timeout_seconds: float = 12.0,
        poll_seconds: float = 0.5,
        monotonic: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._messages = message_proxy
        self._timeout_seconds = max(0.0, float(timeout_seconds))
        self._poll_seconds = max(0.001, float(poll_seconds))
        self._monotonic = monotonic
        self._sleep = sleep

    async def wait(
        self,
        chat_id: str,
        source_message_id: str,
        source_user_id: str,
        source_timestamp: float,
    ) -> bool:
        deadline = self._monotonic() + self._timeout_seconds
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            try:
                records = await asyncio.wait_for(
                    self._read_recent(chat_id),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                return False

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            if self._has_later_bot_reply(
                records,
                source_message_id,
                source_user_id,
                source_timestamp,
            ):
                return True
            await self._sleep(min(self._poll_seconds, remaining))

    async def _read_recent(self, chat_id: str) -> Any:
        try:
            return await self._messages.get_recent(chat_id, limit=8)
        except Exception:
            return None

    @staticmethod
    def _has_later_bot_reply(
        records: Any,
        source_message_id: str,
        source_user_id: str,
        source_timestamp: float,
    ) -> bool:
        if not isinstance(records, (list, tuple)):
            return False
        source_id = str(source_message_id or "").strip()
        source_sender_id = str(source_user_id or "").strip()
        source_time = _parse_timestamp(source_timestamp)
        if source_time is None:
            source_time = 0.0

        for record in records:
            if not isinstance(record, dict):
                continue
            message_id = str(record.get("message_id") or "").strip()
            if not message_id or message_id == source_id:
                continue
            timestamp = _parse_timestamp(record.get("timestamp"))
            if timestamp is None or timestamp <= source_time:
                continue
            info = record.get("message_info")
            user = info.get("user_info") if isinstance(info, dict) else None
            sender_id = (
                str(user.get("user_id") or "").strip()
                if isinstance(user, dict)
                else ""
            )
            if sender_id and sender_id != source_sender_id:
                return True
        return False


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if isfinite(timestamp) else None
