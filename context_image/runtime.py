"""Concurrency and deduplication around generation and delivery."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections import defaultdict
from collections.abc import Awaitable, Callable
from hashlib import sha256
from time import time
from typing import Any

from .errors import BusyError
from .models import GenerationRequest, ImageReference, ReferenceRole
from .parsing import detect_image_mime


class PluginRuntime:
    def __init__(
        self,
        service: Any,
        delivery: Any,
        *,
        max_concurrency: int = 2,
        skip_when_busy: bool = True,
        dedupe_seconds: float = 90,
        image_cache: Any | None = None,
        identity_store: Any | None = None,
        api_key_configured: bool = True,
        max_input_bytes: int = 20 * 1024 * 1024,
        clock=time,
    ) -> None:
        self._service = service
        self._delivery = delivery
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._stream_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._skip_when_busy = skip_when_busy
        self._dedupe_seconds = max(0.0, float(dedupe_seconds))
        self._image_cache = image_cache
        self._identity_store = identity_store
        self._api_key_configured = api_key_configured
        self._max_input_bytes = max(1, int(max_input_bytes))
        self._clock = clock
        self._successes: dict[str, float] = {}

    async def generate(
        self,
        stream_id: str,
        chat_id: str,
        request: GenerationRequest,
        before_delivery: Callable[[], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        fingerprint = self._fingerprint(stream_id, request)
        self._clear_expired()
        if fingerprint in self._successes:
            return {
                "success": True,
                "content": "相同图片请求刚刚已经处理过，不再重复生成。",
                "already_sent": True,
                "deduplicated": True,
            }

        lock = self._stream_locks[stream_id]
        if self._skip_when_busy and lock.locked():
            raise BusyError()

        async with lock:
            self._clear_expired()
            if fingerprint in self._successes:
                return {
                    "success": True,
                    "content": "相同图片请求刚刚已经处理过，不再重复生成。",
                    "already_sent": True,
                    "deduplicated": True,
                }
            async with self._semaphore:
                outcome = await self._service.generate(stream_id, chat_id, request)
                if before_delivery is not None:
                    await before_delivery()
                result = await self._delivery.send(
                    outcome.image,
                    stream_id,
                    outcome.plan.final_prompt,
                    mode=outcome.plan.mode,
                    used_context=outcome.used_context,
                    used_bot_identity=outcome.used_bot_identity,
                )
            self._successes[fingerprint] = self._clock()
            return result

    def has_recent_image(self, stream_id: str) -> bool:
        if self._image_cache is not None:
            has_recent = getattr(self._image_cache, "has_recent", None)
            return bool(has_recent(stream_id)) if callable(has_recent) else False
        has_recent = getattr(self._service, "has_recent_image", None)
        return bool(has_recent(stream_id)) if callable(has_recent) else False

    async def close(self) -> None:
        self._successes.clear()
        self._stream_locks.clear()

    def cache_message_image(self, stream_id: str, message: Any) -> int:
        if self._image_cache is None or not isinstance(message, dict):
            return 0
        segments = message.get("message_segments")
        if not isinstance(segments, list):
            segments = message.get("raw_message")
        if not isinstance(segments, list):
            return 0

        cached = 0
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_type = str(
                segment.get("type")
                or segment.get("seg_type")
                or segment.get("message_type")
                or ""
            ).lower()
            if segment_type not in {"image", "img", "picture"}:
                continue
            raw = segment.get("data") or segment.get("base64") or segment.get("content")
            if isinstance(raw, dict):
                raw = raw.get("data") or raw.get("base64") or raw.get("content")
            data = self._decode_image_value(raw)
            if data is None:
                continue
            try:
                mime_type = detect_image_mime(data)
            except Exception:
                continue
            reference = ImageReference(
                image_id=sha256(data).hexdigest(),
                data=data,
                mime_type=mime_type,
                source="message:recent",
                role=ReferenceRole.BASE,
                timestamp=self._clock(),
            )
            self._image_cache.put(stream_id, reference)
            cached += 1
        return cached

    def status(self) -> dict[str, Any]:
        identity_status = (
            self._identity_store.status()
            if self._identity_store is not None
            else {"configured": False}
        )
        return {
            "ready": self._api_key_configured,
            "api_key_configured": self._api_key_configured,
            "identity_configured": bool(identity_status.get("configured")),
            "identity": identity_status,
        }

    def _decode_image_value(self, raw: Any) -> bytes | None:
        if not isinstance(raw, str):
            return None
        value = raw.strip()
        if value.startswith("data:image/") and ";base64," in value:
            value = value.split(",", 1)[1]
        elif value.startswith(("http://", "https://")):
            return None
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            return None
        if not data or len(data) > self._max_input_bytes:
            return None
        return data

    def _fingerprint(self, stream_id: str, request: GenerationRequest) -> str:
        base_id = str(self._service.base_image_id(stream_id))
        normalized = " ".join(request.request.casefold().split())
        material = "\0".join(
            (stream_id, normalized, request.mode.value, request.subject.value, base_id)
        )
        return sha256(material.encode("utf-8")).hexdigest()

    def _clear_expired(self) -> None:
        now = self._clock()
        expired = [
            key
            for key, timestamp in self._successes.items()
            if now - timestamp > self._dedupe_seconds
        ]
        for key in expired:
            self._successes.pop(key, None)
