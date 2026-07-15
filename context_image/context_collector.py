"""Collect and sanitize recent chat context for prompt compilation."""

from __future__ import annotations

import re
from typing import Any

from .models import ContextSnapshot


_DATA_URL_RE = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9+/]{160,}={0,2}\b")
_AUTH_RE = re.compile(
    r"(?:authorization\s*:\s*bearer|api[_ -]?key\s*[:=])\s*\S+",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"(?<!\w)[A-Za-z]:\\(?:[^\s，。；;]+)")
_POSIX_SENSITIVE_PATH_RE = re.compile(
    r"(?<!\w)/(?:home|root|Users|var|etc|tmp)/[^\s，。；;]+"
)


def sanitize_context(text: str) -> str:
    value = str(text or "")
    value = _DATA_URL_RE.sub("[图片数据已省略]", value)
    value = _LONG_TOKEN_RE.sub("[长数据已省略]", value)
    value = _AUTH_RE.sub("[密钥已省略]", value)
    value = _WINDOWS_PATH_RE.sub("[本地路径已省略]", value)
    value = _POSIX_SENSITIVE_PATH_RE.sub("[本地路径已省略]", value)
    return value.strip()


class ContextCollector:
    def __init__(
        self,
        message_proxy: Any,
        *,
        message_limit: int = 16,
        max_chars: int = 8000,
        include_previous_prompt: bool = True,
    ) -> None:
        self._message = message_proxy
        self._message_limit = max(1, int(message_limit))
        self._max_chars = max(1, int(max_chars))
        self._include_previous_prompt = include_previous_prompt

    async def collect(
        self,
        chat_id: str,
        current_request: str,
        *,
        previous_prompt: str = "",
        reply_text: str = "",
        image_descriptions: tuple[str, ...] = (),
    ) -> ContextSnapshot:
        readable = await self._message.build_readable(
            None,
            chat_id=chat_id,
            limit=self._message_limit,
            replace_bot_name=True,
            timestamp_mode="relative",
            truncate=False,
        )
        recent_text = sanitize_context(readable)
        if len(recent_text) > self._max_chars:
            recent_text = recent_text[-self._max_chars :]

        return ContextSnapshot(
            current_request=sanitize_context(current_request),
            recent_text=recent_text,
            reply_text=sanitize_context(reply_text),
            previous_prompt=(
                sanitize_context(previous_prompt)
                if self._include_previous_prompt
                else ""
            ),
            image_descriptions=tuple(
                sanitized
                for item in image_descriptions
                if (sanitized := sanitize_context(item))
            ),
        )
