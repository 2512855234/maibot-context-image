"""Per-stream FIFO coordination for accepted image generation jobs."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import time
from typing import Any

from .errors import ContextImageError, QueueFullError
from .models import GenerationRequest


@dataclass(frozen=True, slots=True)
class TriggerJob:
    source_message_id: str
    stream_id: str
    chat_id: str
    request: GenerationRequest
    before_delivery: Callable[[], Awaitable[Any]] | None = None
    created_at: float = field(default_factory=time)


@dataclass(slots=True)
class _StreamState:
    active: bool = False
    pending: deque[TriggerJob] = field(default_factory=deque)


class TriggerCoordinator:
    def __init__(
        self,
        runtime: Any,
        error_sender: Callable[[str, str], Awaitable[Any]],
        *,
        max_pending: int = 2,
        dedupe_seconds: float = 90.0,
        clock: Callable[[], float] = time,
    ) -> None:
        self._runtime = runtime
        self._error_sender = error_sender
        self._max_pending = max(0, int(max_pending))
        self._dedupe_seconds = max(0.0, float(dedupe_seconds))
        self._clock = clock
        self._lock = asyncio.Lock()
        self._states: dict[str, _StreamState] = {}
        self._recent: dict[str, float] = {}
        self._workers: set[asyncio.Task[None]] = set()
        self._close_waiter: asyncio.Task[None] | None = None
        self._closed = False

    async def submit(self, job: TriggerJob) -> dict[str, Any]:
        async with self._lock:
            if self._closed:
                return {"success": False, "content": "图片任务队列已关闭。"}

            fingerprint = self._fingerprint(job)
            self._clear_expired_dedupe()
            if fingerprint in self._recent:
                return {
                    "success": True,
                    "queued": False,
                    "deduplicated": True,
                    "content": "该图片请求已在处理中。",
                }

            state = self._states.setdefault(job.stream_id, _StreamState())
            if state.active and len(state.pending) >= self._max_pending:
                raise QueueFullError()

            self._recent[fingerprint] = self._clock()
            if state.active:
                state.pending.append(job)
            else:
                state.active = True
                self._start_worker(job)

            return {
                "success": True,
                "queued": True,
                "content": "图片任务已加入队列。",
            }

    async def close(self) -> None:
        async with self._lock:
            if self._close_waiter is None:
                self._closed = True
                for state in self._states.values():
                    state.pending.clear()
                self._close_waiter = asyncio.create_task(
                    self._wait_for_workers(tuple(self._workers))
                )
            close_waiter = self._close_waiter
        await asyncio.shield(close_waiter)

    @staticmethod
    async def _wait_for_workers(workers: tuple[asyncio.Task[None], ...]) -> None:
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    def _fingerprint(self, job: TriggerJob) -> str:
        source_message_id = str(job.source_message_id or "").strip()
        if source_message_id:
            return f"source:{source_message_id}"
        normalized = " ".join(job.request.request.casefold().split())
        return f"request:{job.stream_id}\0{normalized}"

    def _clear_expired_dedupe(self) -> None:
        now = self._clock()
        expired = [
            fingerprint
            for fingerprint, accepted_at in self._recent.items()
            if now - accepted_at >= self._dedupe_seconds
        ]
        for fingerprint in expired:
            self._recent.pop(fingerprint, None)

    def _start_worker(self, job: TriggerJob) -> None:
        worker = asyncio.create_task(self._run_job(job))
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)

    async def _run_job(self, job: TriggerJob) -> None:
        try:
            await self._runtime.generate(
                job.stream_id,
                job.chat_id,
                job.request,
                before_delivery=job.before_delivery,
            )
        except ContextImageError as exc:
            await self._send_error(exc.public_message, job.stream_id)
        except Exception:
            await self._send_error("图片生成失败，请稍后重试。", job.stream_id)
        finally:
            async with self._lock:
                state = self._states.get(job.stream_id)
                if state is None:
                    return
                if not self._closed and state.pending:
                    self._start_worker(state.pending.popleft())
                else:
                    state.active = False
                    if not state.pending:
                        self._states.pop(job.stream_id, None)

    async def _send_error(self, content: str, stream_id: str) -> None:
        try:
            await self._error_sender(content, stream_id)
        except Exception:
            pass
