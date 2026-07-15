import asyncio
from unittest import IsolatedAsyncioTestCase

from context_image.errors import ContextImageError, QueueFullError
from context_image.models import GenerationRequest
from context_image.trigger_coordinator import TriggerCoordinator, TriggerJob


def request(text: str) -> GenerationRequest:
    return GenerationRequest.from_mapping({"request": text})


def job(
    text: str,
    *,
    stream_id: str = "stream-1",
    source_message_id: str = "",
    before_delivery=None,
) -> TriggerJob:
    return TriggerJob(
        source_message_id=source_message_id,
        stream_id=stream_id,
        chat_id=f"chat-for-{stream_id}",
        request=request(text),
        before_delivery=before_delivery,
    )


class ControlledRuntime:
    def __init__(self) -> None:
        self.calls = []
        self.started = asyncio.Queue()
        self.releases = {}
        self.cancelled = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def generate(
        self,
        stream_id,
        chat_id,
        generation_request,
        *,
        before_delivery=None,
    ):
        text = generation_request.request
        self.calls.append((stream_id, chat_id, text, before_delivery))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        release = self.releases.setdefault(text, asyncio.Event())
        await self.started.put(text)
        try:
            await release.wait()
            if before_delivery is not None:
                await before_delivery()
            return {"success": True}
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.active -= 1

    def release(self, text: str) -> None:
        self.releases[text].set()


class RecordingErrorSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = []
        self.fail = fail

    async def __call__(self, content, stream_id):
        self.calls.append((content, stream_id))
        if self.fail:
            raise RuntimeError("send failed")


class TriggerCoordinatorTests(IsolatedAsyncioTestCase):
    async def test_one_active_plus_two_pending_accepts_and_fourth_rejects(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(runtime, RecordingErrorSender(), max_pending=2)

        first = await coordinator.submit(job("one", source_message_id="m1"))
        self.assertEqual(await runtime.started.get(), "one")
        second = await coordinator.submit(job("two", source_message_id="m2"))
        third = await coordinator.submit(job("three", source_message_id="m3"))

        with self.assertRaisesRegex(QueueFullError, "图片任务队列已满"):
            await coordinator.submit(job("four", source_message_id="m4"))

        self.assertTrue(first["queued"])
        self.assertTrue(second["queued"])
        self.assertTrue(third["queued"])
        self.assertEqual([call[2] for call in runtime.calls], ["one"])

        runtime.release("one")
        self.assertEqual(await runtime.started.get(), "two")
        runtime.release("two")
        self.assertEqual(await runtime.started.get(), "three")
        runtime.release("three")
        await coordinator.close()

    async def test_pending_jobs_run_in_fifo_order_and_forward_delivery_gate(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(runtime, RecordingErrorSender())
        gates = []

        async def gate():
            gates.append("second-gate")

        await coordinator.submit(job("one", source_message_id="m1"))
        self.assertEqual(await runtime.started.get(), "one")
        await coordinator.submit(
            job("two", source_message_id="m2", before_delivery=gate)
        )
        await coordinator.submit(job("three", source_message_id="m3"))

        runtime.release("one")
        self.assertEqual(await runtime.started.get(), "two")
        runtime.release("two")
        self.assertEqual(await runtime.started.get(), "three")
        runtime.release("three")
        await coordinator.close()

        self.assertEqual([call[2] for call in runtime.calls], ["one", "two", "three"])
        self.assertIs(runtime.calls[1][3], gate)
        self.assertEqual(gates, ["second-gate"])

    async def test_source_message_id_deduplicates_active_request_before_capacity(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(runtime, RecordingErrorSender(), max_pending=0)

        await coordinator.submit(job("first text", source_message_id="same-message"))
        self.assertEqual(await runtime.started.get(), "first text")
        duplicate = await coordinator.submit(
            job("different text", source_message_id="same-message")
        )

        self.assertEqual(
            duplicate,
            {
                "success": True,
                "queued": False,
                "deduplicated": True,
                "content": "该图片请求已在处理中。",
            },
        )
        self.assertEqual(len(runtime.calls), 1)

        runtime.release("first text")
        await coordinator.close()

    async def test_fallback_dedupe_normalizes_case_and_whitespace_per_stream(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(runtime, RecordingErrorSender())

        await coordinator.submit(job("  Draw   CAT  "))
        self.assertEqual(await runtime.started.get(), "Draw   CAT")
        duplicate = await coordinator.submit(job("draw cat"))
        other_stream = await coordinator.submit(job("draw cat", stream_id="stream-2"))

        self.assertTrue(duplicate["deduplicated"])
        self.assertTrue(other_stream["queued"])
        self.assertEqual(await runtime.started.get(), "draw cat")

        runtime.release("Draw   CAT")
        runtime.release("draw cat")
        await coordinator.close()

    async def test_dedupe_ttl_expiry_allows_request_again(self):
        now = [10.0]
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(
            runtime,
            RecordingErrorSender(),
            dedupe_seconds=5,
            clock=lambda: now[0],
        )

        await coordinator.submit(job("one", source_message_id="m1"))
        self.assertEqual(await runtime.started.get(), "one")
        runtime.release("one")
        await asyncio.sleep(0)
        duplicate = await coordinator.submit(job("one", source_message_id="m1"))
        self.assertTrue(duplicate["deduplicated"])

        now[0] = 15.01
        accepted = await coordinator.submit(job("one", source_message_id="m1"))
        self.assertTrue(accepted["queued"])
        self.assertEqual(await runtime.started.get(), "one")
        runtime.release("one")
        await coordinator.close()

    async def test_dedupe_expires_at_exact_ttl_boundary(self):
        now = [10.0]
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(
            runtime,
            RecordingErrorSender(),
            dedupe_seconds=5,
            clock=lambda: now[0],
        )

        await coordinator.submit(job("first", source_message_id="same"))
        self.assertEqual(await runtime.started.get(), "first")
        now[0] = 15.0

        accepted = await coordinator.submit(job("second", source_message_id="same"))

        self.assertTrue(accepted["queued"])
        self.assertNotIn("deduplicated", accepted)
        runtime.release("first")
        self.assertEqual(await runtime.started.get(), "second")
        runtime.release("second")
        await coordinator.close()

    async def test_zero_dedupe_window_accepts_repeat_in_same_clock_tick(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(
            runtime,
            RecordingErrorSender(),
            dedupe_seconds=0,
            clock=lambda: 10.0,
        )

        await coordinator.submit(job("first", source_message_id="same"))
        self.assertEqual(await runtime.started.get(), "first")

        accepted = await coordinator.submit(job("second", source_message_id="same"))

        self.assertTrue(accepted["queued"])
        self.assertNotIn("deduplicated", accepted)
        runtime.release("first")
        self.assertEqual(await runtime.started.get(), "second")
        runtime.release("second")
        await coordinator.close()

    async def test_different_streams_run_concurrently(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(runtime, RecordingErrorSender())

        await coordinator.submit(job("one", stream_id="stream-1", source_message_id="m1"))
        await coordinator.submit(job("two", stream_id="stream-2", source_message_id="m2"))
        started = {await runtime.started.get(), await runtime.started.get()}

        self.assertEqual(started, {"one", "two"})
        self.assertEqual(runtime.max_active, 2)

        runtime.release("one")
        runtime.release("two")
        await coordinator.close()

    async def test_public_and_unexpected_errors_send_safe_text_and_fifo_continues(self):
        class FailingRuntime:
            def __init__(self):
                self.calls = []
                self.done = asyncio.Event()

            async def generate(self, stream_id, chat_id, generation_request, **kwargs):
                text = generation_request.request
                self.calls.append(text)
                if text == "public":
                    raise ContextImageError("可公开的错误。")
                if text == "secret":
                    raise RuntimeError("API token secret")
                self.done.set()
                return {"success": True}

        runtime = FailingRuntime()
        sender = RecordingErrorSender()
        coordinator = TriggerCoordinator(runtime, sender)

        await coordinator.submit(job("public", source_message_id="m1"))
        await coordinator.submit(job("secret", source_message_id="m2"))
        await coordinator.submit(job("success", source_message_id="m3"))
        await asyncio.wait_for(runtime.done.wait(), 1)
        await coordinator.close()

        self.assertEqual(runtime.calls, ["public", "secret", "success"])
        self.assertEqual(
            sender.calls,
            [
                ("可公开的错误。", "stream-1"),
                ("图片生成失败，请稍后重试。", "stream-1"),
            ],
        )
        self.assertNotIn("secret", repr(sender.calls))

    async def test_error_sender_failure_does_not_stall_fifo(self):
        class FirstFailsRuntime:
            def __init__(self):
                self.calls = []
                self.done = asyncio.Event()

            async def generate(self, stream_id, chat_id, generation_request, **kwargs):
                self.calls.append(generation_request.request)
                if generation_request.request == "first":
                    raise ContextImageError("公开错误")
                self.done.set()
                return {"success": True}

        runtime = FirstFailsRuntime()
        sender = RecordingErrorSender(fail=True)
        coordinator = TriggerCoordinator(runtime, sender)

        await coordinator.submit(job("first", source_message_id="m1"))
        await coordinator.submit(job("second", source_message_id="m2"))
        await asyncio.wait_for(runtime.done.wait(), 1)
        await coordinator.close()

        self.assertEqual(runtime.calls, ["first", "second"])
        self.assertEqual(sender.calls, [("公开错误", "stream-1")])

    async def test_close_clears_waiting_awaits_active_rejects_new_and_is_idempotent(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(runtime, RecordingErrorSender())

        await coordinator.submit(job("active", source_message_id="m1"))
        self.assertEqual(await runtime.started.get(), "active")
        await coordinator.submit(job("waiting", source_message_id="m2"))

        closing = asyncio.create_task(coordinator.close())
        await asyncio.sleep(0)
        self.assertFalse(closing.done())

        rejected = await coordinator.submit(job("later", source_message_id="m3"))
        self.assertEqual(
            rejected,
            {"success": False, "content": "图片任务队列已关闭。"},
        )

        runtime.release("active")
        await asyncio.wait_for(closing, 1)
        await coordinator.close()

        self.assertEqual([call[2] for call in runtime.calls], ["active"])

    async def test_cancelled_close_caller_does_not_cancel_active_worker(self):
        runtime = ControlledRuntime()
        coordinator = TriggerCoordinator(runtime, RecordingErrorSender())

        await coordinator.submit(job("active", source_message_id="m1"))
        self.assertEqual(await runtime.started.get(), "active")

        closing = asyncio.create_task(coordinator.close())
        await asyncio.sleep(0)
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        await asyncio.sleep(0)

        self.assertFalse(runtime.cancelled.is_set())
        self.assertEqual(
            await coordinator.submit(job("later", source_message_id="m2")),
            {"success": False, "content": "图片任务队列已关闭。"},
        )

        later_close = asyncio.create_task(coordinator.close())
        await asyncio.sleep(0)
        self.assertFalse(later_close.done())
        runtime.release("active")
        await asyncio.wait_for(later_close, 1)
        self.assertFalse(runtime.cancelled.is_set())
