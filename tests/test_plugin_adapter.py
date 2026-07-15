import asyncio
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from context_image.models import (
    CaptureType,
    GenerationMode,
    ImageIntentDecision,
    Subject,
)


class FakeLogger:
    def __init__(self):
        self.exceptions = []

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        self.exceptions.append((args, kwargs))


class FakeRuntime:
    def __init__(self, *, recent=False, events=None, close_error=None, cache_error=None):
        self.cached_messages = []
        self.generate_calls = []
        self.has_recent_calls = []
        self.status_calls = 0
        self.recent = recent
        self.events = events
        self.close_error = close_error
        self.cache_error = cache_error

    async def generate(self, stream_id, chat_id, request, before_delivery=None):
        self.generate_calls.append((stream_id, chat_id, request, before_delivery))
        if self.events is not None:
            self.events.append("generate")
        return {"success": True, "content": "ok", "already_sent": True}

    def cache_message_image(self, stream_id, message):
        if self.cache_error is not None:
            raise self.cache_error
        self.cached_messages.append((stream_id, message))
        return 1

    def has_recent_image(self, stream_id):
        self.has_recent_calls.append(stream_id)
        return self.recent

    def status(self):
        self.status_calls += 1
        return {
            "ready": True,
            "api_key_configured": True,
            "identity_configured": True,
        }

    async def close(self):
        if self.events is not None:
            self.events.append("runtime")
        if self.close_error is not None:
            raise self.close_error


class FakeDetector:
    def __init__(self, decision=None, *, error=None):
        self.decision = decision or ImageIntentDecision.no_trigger()
        self.error = error
        self.calls = []

    async def detect(self, text, *, has_recent_image=False):
        self.calls.append((text, has_recent_image))
        if self.error is not None:
            raise self.error
        return self.decision


class FakeReplyGate:
    def __init__(self):
        self.calls = []

    async def wait(self, chat_id, message_id, user_id, timestamp):
        self.calls.append((chat_id, message_id, user_id, timestamp))
        return True


class FakeCoordinator:
    def __init__(self, *, error=None, events=None, close_error=None):
        self.jobs = []
        self.error = error
        self.events = events
        self.close_error = close_error

    async def submit(self, job):
        self.jobs.append(job)
        if self.error is not None:
            raise self.error
        return {"success": True, "queued": True, "content": "queued"}

    async def close(self):
        if self.events is not None:
            self.events.append("coordinator")
        if self.close_error is not None:
            raise self.close_error


class FakeHttp:
    def __init__(self, events=None, close_error=None):
        self.events = events
        self.close_error = close_error

    async def aclose(self):
        if self.events is not None:
            self.events.append("http")
        if self.close_error is not None:
            raise self.close_error


def private_message(**overrides):
    message = {
        "message_id": "message-1",
        "timestamp": 100.0,
        "session_id": "session-1",
        "processed_plain_text": "我想看你穿汉服的样子",
        "message_info": {
            "user_info": {"user_id": "user-1"},
            "group_info": None,
        },
        "is_command": False,
        "raw_message": [{"type": "text", "data": "我想看你穿汉服的样子"}],
    }
    message.update(overrides)
    return message


def load_plugin_module():
    plugin_root = Path(__file__).resolve().parents[1]
    module_name = "maibot_plugin_com_maibot_context_image_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        plugin_root / "plugin.py",
        submodule_search_locations=[str(plugin_root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法构造插件加载 spec")
    sys.modules.pop(module_name, None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def configured_plugin(module, *, auto_enabled=True):
    plugin = module.ContextImagePlugin()
    config = plugin.build_default_config()
    config["auto_trigger"]["enabled"] = auto_enabled
    plugin.set_plugin_config(config)
    plugin._ctx = SimpleNamespace(logger=FakeLogger())
    return plugin


class PluginAdapterTests(IsolatedAsyncioTestCase):
    async def test_after_process_hook_uses_serialized_session_id(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime(recent=False)
        plugin._intent_detector = FakeDetector(
            ImageIntentDecision(
                True,
                1.0,
                "generate",
                Subject.BOT,
                CaptureType.SELFIE,
                GenerationMode.IDENTITY_REFERENCE,
                "给我拍一张你现在的照片",
            )
        )
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()
        handler = getattr(plugin, "handle_after_process_hook", None)

        self.assertIsNotNone(handler)
        await handler(
            message=private_message(
                session_id="hook-stream",
                processed_plain_text="给我拍一张你现在的照片",
            )
        )

        self.assertEqual(plugin._runtime.has_recent_calls, ["hook-stream"])
        self.assertEqual(len(plugin._coordinator.jobs), 1)
        job = plugin._coordinator.jobs[0]
        self.assertEqual(job.stream_id, "hook-stream")
        self.assertEqual(job.chat_id, "hook-stream")
        self.assertIs(job.request.subject, Subject.BOT)

    async def test_reported_request_event_does_not_plan_or_enqueue(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime()

        class PlannerMustNotRun:
            def __init__(self):
                self.calls = []

            async def generate(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("reported request reached Planner")

        planner = PlannerMustNotRun()
        plugin._intent_detector = module.IntentDetector(planner)
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()
        message = private_message(
            processed_plain_text="他刚才说：给我拍一张你现在的照片"
        )

        await plugin.handle_message(message, stream_id="stream-1")

        self.assertEqual(planner.calls, [])
        self.assertEqual(plugin._coordinator.jobs, [])
        self.assertEqual(plugin._runtime.generate_calls, [])

    async def test_first_person_discourse_events_enqueue_without_planner(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime()

        class PlannerMustNotRun:
            def __init__(self):
                self.calls = []

            async def generate(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("direct request reached Planner")

        planner = PlannerMustNotRun()
        plugin._intent_detector = module.IntentDetector(planner)
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()

        for index, text in enumerate(
            (
                "我想说：给我拍一张你现在的照片",
                "我跟你说：给我拍一张你现在的照片",
            ),
            start=1,
        ):
            await plugin.handle_message(
                private_message(
                    message_id=f"message-{index}",
                    processed_plain_text=text,
                ),
                stream_id="stream-1",
            )

        self.assertEqual(planner.calls, [])
        self.assertEqual(len(plugin._coordinator.jobs), 2)
        self.assertEqual(
            [job.request.subject.value for job in plugin._coordinator.jobs],
            [Subject.BOT.value, Subject.BOT.value],
        )
        self.assertEqual(plugin._runtime.generate_calls, [])

    async def test_private_outfit_message_submits_bot_full_body_job_with_reply_gate(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime(recent=False)
        plugin._intent_detector = FakeDetector(
            ImageIntentDecision(
                True,
                1.0,
                "generate",
                Subject.BOT,
                CaptureType.FULL_BODY,
                GenerationMode.IDENTITY_REFERENCE,
                "麦麦穿汉服",
            )
        )
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()
        message = private_message()

        result = await plugin.handle_message(message, stream_id="stream-1")

        self.assertIsNone(result)
        self.assertEqual(plugin._runtime.cached_messages, [("stream-1", message)])
        self.assertEqual(plugin._intent_detector.calls, [(message["processed_plain_text"], False)])
        self.assertEqual(len(plugin._coordinator.jobs), 1)
        job = plugin._coordinator.jobs[0]
        self.assertEqual(job.source_message_id, "message-1")
        self.assertEqual(job.stream_id, "stream-1")
        self.assertEqual(job.chat_id, "stream-1")
        self.assertIs(job.request.subject, Subject.BOT)
        self.assertIs(job.request.capture_type, CaptureType.FULL_BODY)
        self.assertIs(job.request.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertIsNotNone(job.before_delivery)
        self.assertTrue(await job.before_delivery())
        self.assertEqual(
            plugin._reply_gate.calls,
            [("stream-1", "message-1", "user-1", 100.0)],
        )

    async def test_auto_job_captures_reply_gate_present_at_submission(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime()
        plugin._intent_detector = FakeDetector(
            ImageIntentDecision(
                True,
                1.0,
                "generate",
                Subject.BOT,
                CaptureType.FULL_BODY,
                GenerationMode.IDENTITY_REFERENCE,
                "麦麦穿汉服",
            )
        )
        original_gate = FakeReplyGate()
        plugin._reply_gate = original_gate
        plugin._coordinator = FakeCoordinator()

        await plugin.handle_message(private_message(), stream_id="stream-1")
        job = plugin._coordinator.jobs[0]
        plugin._reply_gate = None

        self.assertTrue(await job.before_delivery())
        self.assertEqual(
            original_gate.calls,
            [("stream-1", "message-1", "user-1", 100.0)],
        )

    async def test_group_message_has_zero_cache_detect_recent_or_submit_effects(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime(recent=True)
        plugin._intent_detector = FakeDetector()
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()
        message = private_message(
            message_info={
                "user_info": {"user_id": "user-1"},
                "group_info": {"group_id": "group-1"},
            }
        )

        await plugin.handle_message(message, stream_id="stream-1")

        self.assertEqual(plugin._runtime.cached_messages, [])
        self.assertEqual(plugin._runtime.has_recent_calls, [])
        self.assertEqual(plugin._intent_detector.calls, [])
        self.assertEqual(plugin._coordinator.jobs, [])

    async def test_auto_disabled_caches_private_image_but_does_not_classify(self):
        module = load_plugin_module()
        plugin = configured_plugin(module, auto_enabled=False)
        plugin._runtime = FakeRuntime(recent=True)
        plugin._intent_detector = FakeDetector()
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()
        message = private_message(
            raw_message=[
                {"type": "text", "data": "我想看你穿汉服的样子"},
                {"type": "image", "data": "abc"},
            ]
        )

        await plugin.handle_message(message, stream_id="stream-1")

        self.assertEqual(plugin._runtime.cached_messages, [("stream-1", message)])
        self.assertEqual(plugin._runtime.has_recent_calls, [])
        self.assertEqual(plugin._intent_detector.calls, [])
        self.assertEqual(plugin._coordinator.jobs, [])

    async def test_private_image_only_message_is_cached_without_generation(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime(recent=True)
        plugin._intent_detector = FakeDetector()
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()
        message = private_message(
            processed_plain_text="",
            raw_message=[{"type": "image", "data": "abc"}],
        )

        await plugin.handle_message(message, stream_id="stream-1")

        self.assertEqual(plugin._runtime.cached_messages, [("stream-1", message)])
        self.assertEqual(plugin._intent_detector.calls, [])
        self.assertEqual(plugin._coordinator.jobs, [])

    async def test_detector_and_submission_errors_do_not_escape_message_event(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime()
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = FakeCoordinator()
        plugin._intent_detector = FakeDetector(error=RuntimeError("private detail"))

        await plugin.handle_message(private_message(), stream_id="stream-1")

        plugin._intent_detector = FakeDetector(
            ImageIntentDecision(
                True,
                1.0,
                "generate",
                Subject.BOT,
                CaptureType.FULL_BODY,
                GenerationMode.IDENTITY_REFERENCE,
                "request",
            )
        )
        plugin._coordinator = FakeCoordinator(error=RuntimeError("private detail"))
        await plugin.handle_message(private_message(), stream_id="stream-1")
        self.assertEqual(len(plugin.ctx.logger.exceptions), 2)

    async def test_cache_parser_and_config_errors_do_not_escape_or_leak(self):
        module = load_plugin_module()
        message = private_message()

        cache_plugin = configured_plugin(module)
        cache_plugin._runtime = FakeRuntime(
            cache_error=RuntimeError("cache private detail")
        )
        cache_plugin._intent_detector = FakeDetector()
        cache_plugin._reply_gate = FakeReplyGate()
        cache_plugin._coordinator = FakeCoordinator()
        self.assertIsNone(
            await cache_plugin.handle_message(message, stream_id="stream-1")
        )

        parser_plugin = configured_plugin(module)
        parser_plugin._runtime = FakeRuntime()
        parser_plugin._intent_detector = FakeDetector()
        parser_plugin._reply_gate = FakeReplyGate()
        parser_plugin._coordinator = FakeCoordinator()
        with patch.object(
            module,
            "parse_trigger_message",
            side_effect=RuntimeError("parser private detail"),
        ):
            self.assertIsNone(
                await parser_plugin.handle_message(message, stream_id="stream-1")
            )

        config_plugin = module.ContextImagePlugin()
        config_plugin._ctx = SimpleNamespace(logger=FakeLogger())
        config_plugin._runtime = FakeRuntime()
        config_plugin._intent_detector = FakeDetector()
        config_plugin._reply_gate = FakeReplyGate()
        config_plugin._coordinator = FakeCoordinator()
        self.assertIsNone(
            await config_plugin.handle_message(message, stream_id="stream-1")
        )

        for plugin in (cache_plugin, parser_plugin, config_plugin):
            self.assertEqual(len(plugin.ctx.logger.exceptions), 1)
            logged = repr(plugin.ctx.logger.exceptions)
            self.assertNotIn("private detail", logged)
            self.assertEqual(plugin._coordinator.jobs, [])

    async def test_tool_and_commands_reject_group_before_request_construction(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime()
        plugin._coordinator = FakeCoordinator()

        with patch.object(
            module.GenerationRequest,
            "from_mapping",
            side_effect=AssertionError("request constructed for group"),
        ) as from_mapping:
            tool = await plugin.handle_generate_image(
                request="一只猫",
                stream_id="stream-1",
                group_id="group-1",
            )
            draw = await plugin.handle_draw_command(
                stream_id="stream-1",
                group_id="group-1",
                matched_groups={"request": "一只猫"},
            )
            edit = await plugin.handle_edit_command(
                stream_id="stream-1",
                group_id="group-1",
                matched_groups={"request": "下雪"},
            )

        self.assertFalse(tool["success"])
        self.assertFalse(draw[0])
        self.assertFalse(edit[0])
        self.assertEqual(from_mapping.call_count, 0)
        self.assertEqual(plugin._coordinator.jobs, [])
        self.assertEqual(plugin._runtime.generate_calls, [])

    async def test_status_command_rejects_group_without_runtime_work(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime()

        result = await plugin.handle_status_command(
            stream_id="stream-1", group_id="group-1"
        )

        self.assertFalse(result[0])
        self.assertEqual(plugin._runtime.status_calls, 0)

    async def test_tool_and_commands_enqueue_without_calling_runtime(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        plugin._runtime = FakeRuntime()
        plugin._coordinator = FakeCoordinator()

        tool = await plugin.handle_generate_image(
            request="拍一张你现在的照片",
            mode="auto",
            subject="bot",
            stream_id="stream-1",
            message_id="tool-message",
        )
        draw = await plugin.handle_draw_command(
            stream_id="stream-1",
            message_id="draw-message",
            matched_groups={"request": "一只橘猫"},
        )
        edit = await plugin.handle_edit_command(
            stream_id="stream-1",
            message_id="edit-message",
            matched_groups={"request": "改成下雪"},
        )

        self.assertTrue(tool["success"])
        self.assertEqual(len(draw), 3)
        self.assertTrue(draw[0])
        self.assertTrue(edit[0])
        self.assertEqual(plugin._runtime.generate_calls, [])
        self.assertEqual(
            [job.source_message_id for job in plugin._coordinator.jobs],
            ["tool-message", "draw-message", "edit-message"],
        )
        self.assertEqual(
            plugin._coordinator.jobs[0].request.subject.value,
            Subject.BOT.value,
        )
        self.assertEqual(
            plugin._coordinator.jobs[2].request.mode.value,
            GenerationMode.IMAGE_TO_IMAGE.value,
        )
        self.assertTrue(
            all(job.before_delivery is None for job in plugin._coordinator.jobs)
        )

    async def test_tool_and_event_duplicate_source_id_create_one_runtime_job(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        runtime = FakeRuntime()
        plugin._runtime = runtime
        plugin._intent_detector = FakeDetector(
            ImageIntentDecision(
                True,
                1.0,
                "generate",
                Subject.BOT,
                CaptureType.FULL_BODY,
                GenerationMode.IDENTITY_REFERENCE,
                "我想看你穿汉服的样子",
            )
        )
        plugin._reply_gate = FakeReplyGate()
        plugin._coordinator = module.TriggerCoordinator(
            runtime,
            lambda content, stream_id: asyncio.sleep(0),
            max_pending=2,
            dedupe_seconds=90,
        )

        await plugin.handle_generate_image(
            request="我想看你穿汉服的样子",
            stream_id="stream-1",
            message_id="shared-message",
        )
        await plugin.handle_message(
            private_message(message_id="shared-message"),
            stream_id="stream-1",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(len(runtime.generate_calls), 1)
        await plugin._coordinator.close()

    async def test_close_order_is_coordinator_runtime_http_and_idempotent(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        events = []
        plugin._coordinator = FakeCoordinator(events=events)
        plugin._runtime = FakeRuntime(events=events)
        plugin._http = FakeHttp(events=events)
        plugin._intent_detector = object()
        plugin._reply_gate = object()

        await plugin._close_runtime()
        await plugin._close_runtime()

        self.assertEqual(events, ["coordinator", "runtime", "http"])
        self.assertIsNone(plugin._coordinator)
        self.assertIsNone(plugin._runtime)
        self.assertIsNone(plugin._http)
        self.assertIsNone(plugin._intent_detector)
        self.assertIsNone(plugin._reply_gate)

    async def test_close_attempts_all_stages_and_clears_state_after_each_stage_failure(self):
        module = load_plugin_module()

        for failing_stage in ("coordinator", "runtime", "http"):
            with self.subTest(failing_stage=failing_stage):
                plugin = configured_plugin(module)
                events = []
                errors = {
                    stage: RuntimeError(f"{stage}-close-failed")
                    if stage == failing_stage
                    else None
                    for stage in ("coordinator", "runtime", "http")
                }
                plugin._coordinator = FakeCoordinator(
                    events=events,
                    close_error=errors["coordinator"],
                )
                plugin._runtime = FakeRuntime(
                    events=events,
                    close_error=errors["runtime"],
                )
                plugin._http = FakeHttp(
                    events=events,
                    close_error=errors["http"],
                )
                plugin._intent_detector = object()
                plugin._reply_gate = object()

                with self.assertRaisesRegex(
                    RuntimeError,
                    f"{failing_stage}-close-failed",
                ):
                    await plugin._close_runtime()

                self.assertEqual(events, ["coordinator", "runtime", "http"])
                self.assertIsNone(plugin._coordinator)
                self.assertIsNone(plugin._runtime)
                self.assertIsNone(plugin._http)
                self.assertIsNone(plugin._intent_detector)
                self.assertIsNone(plugin._reply_gate)
                await plugin._close_runtime()
                self.assertEqual(events, ["coordinator", "runtime", "http"])

    async def test_close_reraises_first_error_after_all_cleanup_attempts(self):
        module = load_plugin_module()
        plugin = configured_plugin(module)
        events = []
        first_error = RuntimeError("coordinator-first")
        plugin._coordinator = FakeCoordinator(
            events=events,
            close_error=first_error,
        )
        plugin._runtime = FakeRuntime(
            events=events,
            close_error=RuntimeError("runtime-second"),
        )
        plugin._http = FakeHttp(
            events=events,
            close_error=RuntimeError("http-third"),
        )

        with self.assertRaises(RuntimeError) as raised:
            await plugin._close_runtime()

        self.assertIs(raised.exception, first_error)
        self.assertEqual(events, ["coordinator", "runtime", "http"])

    async def test_rebuild_constructs_exact_task6_dependencies_and_safe_sender(self):
        module = load_plugin_module()
        plugin = module.ContextImagePlugin()
        config = plugin.build_default_config()
        config["identity"].update(
            character_name="小雨",
            reference_policy="fixed_only",
            bot_aliases=["你", "小雨"],
        )
        config["auto_trigger"].update(
            planner_model="planner-task",
            semantic_threshold=0.87,
            max_pending_per_chat=2,
            reply_wait_seconds=7.5,
            dedupe_seconds=66.0,
        )
        plugin.set_plugin_config(config)
        sent = []

        class Send:
            async def text(self, content, stream_id):
                sent.append((content, stream_id))
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            llm = object()
            message = object()
            plugin._ctx = SimpleNamespace(
                paths=SimpleNamespace(data_dir=root / "data", runtime_dir=root / "runtime"),
                llm=llm,
                message=message,
                send=Send(),
                logger=FakeLogger(),
            )
            captured = {}

            def record(name, result):
                def factory(*args, **kwargs):
                    captured[name] = (args, kwargs)
                    return result
                return factory

            runtime = FakeRuntime()
            coordinator = FakeCoordinator()
            http = FakeHttp()
            with (
                patch.object(module.httpx, "AsyncClient", return_value=http),
                patch.object(module, "ContextCollector", record("collector", object())),
                patch.object(module, "PromptCompiler", record("compiler", object())),
                patch.object(module, "IdentityStore", record("identity_store", object())),
                patch.object(module, "RecentImageCache", record("image_cache", object())),
                patch.object(module, "ImagesApiClient", record("images_api", object())),
                patch.object(module, "GenerationService", record("service", object())),
                patch.object(module, "Delivery", record("delivery", object())),
                patch.object(module, "PluginRuntime", record("runtime", runtime)),
                patch.object(module, "IntentDetector", record("detector", object())),
                patch.object(module, "ReplyGate", record("reply_gate", object())),
                patch.object(module, "TriggerCoordinator", record("coordinator", coordinator)),
            ):
                await plugin._rebuild_runtime()

            detector_args, detector_kwargs = captured["detector"]
            self.assertIs(detector_args[0], llm)
            self.assertEqual(detector_kwargs["planner_model"], "planner-task")
            self.assertEqual(detector_kwargs["semantic_threshold"], 0.87)
            self.assertEqual(detector_kwargs["bot_aliases"], ["你", "小雨"])
            self.assertEqual(detector_kwargs["character_name"], "小雨")
            gate_args, gate_kwargs = captured["reply_gate"]
            self.assertIs(gate_args[0], message)
            self.assertEqual(gate_kwargs["timeout_seconds"], 7.5)
            _, service_kwargs = captured["service"]
            self.assertEqual(service_kwargs["character_name"], "小雨")
            self.assertEqual(service_kwargs["reference_policy"], "fixed_only")
            coordinator_args, coordinator_kwargs = captured["coordinator"]
            self.assertIs(coordinator_args[0], runtime)
            self.assertEqual(coordinator_kwargs["max_pending"], 2)
            self.assertEqual(coordinator_kwargs["dedupe_seconds"], 66.0)
            await coordinator_args[1]("安全错误", "stream-1")
            self.assertEqual(sent, [("安全错误", "stream-1")])


class PluginFactoryTests(TestCase):
    def test_all_components_are_private_and_slow_timeouts_are_preserved(self):
        module = load_plugin_module()
        components = {
            component["name"]: component
            for component in module.ContextImagePlugin().get_components()
        }

        self.assertEqual(
            set(components),
            {
                "generate_image",
                "draw_image",
                "edit_image",
                "image_status",
                "cache_message_images",
                "context_image_after_process",
            },
        )
        for component in components.values():
            self.assertEqual(component["metadata"].get("chat_scope"), "private")
        for name in ("generate_image", "draw_image", "edit_image"):
            self.assertGreaterEqual(
                components[name]["metadata"].get("timeout_ms", 0),
                180_000,
            )

    def test_loads_in_isolated_maibot_runner(self):
        plugin_root = Path(__file__).resolve().parents[1]
        script = f"""
import importlib.util
import sys
from pathlib import Path

root = Path({str(plugin_root)!r})
name = "maibot_plugin_com_maibot_context_image_isolated"
spec = importlib.util.spec_from_file_location(
    name,
    root / "plugin.py",
    submodule_search_locations=[str(root)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
assert module.create_plugin().__class__.__name__ == "ContextImagePlugin"
"""

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=plugin_root.parent,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_create_plugin_starts_with_task6_dependencies_unconstructed(self):
        module = load_plugin_module()

        plugin = module.create_plugin()

        self.assertIsInstance(plugin, module.ContextImagePlugin)
        self.assertIsNone(plugin._intent_detector)
        self.assertIsNone(plugin._reply_gate)
        self.assertIsNone(plugin._coordinator)
