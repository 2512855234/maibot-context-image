import asyncio
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from context_image.delivery import Delivery
from context_image.errors import (
    BusyError,
    ConfigurationError,
    IdentityRequiredError,
    ImageSourceRequiredError,
)
from context_image.generation_service import GenerationService
from context_image.identity import IdentityStore
from context_image.image_sources import RecentImageCache
from context_image.models import (
    CompiledPrompt,
    ContextSnapshot,
    GeneratedImage,
    GenerationMode,
    GenerationOutcome,
    GenerationPlan,
    GenerationRequest,
    ImageReference,
    ReferenceRole,
    Subject,
)
from context_image.runtime import PluginRuntime


VALID_PNG = b"\x89PNG\r\n\x1a\ncontent"


def reference(image_id="identity", role=ReferenceRole.IDENTITY):
    return ImageReference(
        image_id=image_id,
        data=VALID_PNG,
        mime_type="image/png",
        source=image_id,
        role=role,
    )


class FakeContextCollector:
    def __init__(self):
        self.calls = []

    async def collect(self, chat_id, current_request, **kwargs):
        self.calls.append((chat_id, current_request, kwargs))
        return ContextSnapshot(
            current_request=current_request,
            recent_text="Bot 正在窗边听雨",
            previous_prompt=kwargs.get("previous_prompt", ""),
        )


class FakeCompiler:
    def __init__(self):
        self.calls = []

    async def compile(self, snapshot, request, subject):
        self.calls.append((snapshot, request, subject))
        return CompiledPrompt(
            subject=subject,
            scene="雨夜窗边",
            style="写实生活摄影",
            must_preserve=(
                ("固定身份图中的面部特征",)
                if subject is Subject.BOT
                else ()
            ),
        )


class FakeIdentityStore:
    def __init__(self, image=None):
        self.image = image

    def load(self):
        if self.image is None:
            raise IdentityRequiredError()
        return self.image


class FakeImageCache:
    def __init__(self, image=None):
        self.image = image

    def latest(self, stream_id):
        return self.image


class FakeImagesApi:
    def __init__(self):
        self.calls = []

    async def generate(self, plan):
        self.calls.append(plan)
        return GeneratedImage(VALID_PNG, "image/png", "images")


class FakeSender:
    def __init__(self, result=True):
        self.result = result
        self.images = []

    async def image(self, image_data, stream_id):
        self.images.append((image_data, stream_id))
        return self.result


def bot_request():
    return GenerationRequest.from_mapping(
        {"request": "拍一张你现在的照片", "subject": "bot"}
    )


def cat_request():
    return GenerationRequest.from_mapping({"request": "画一只橘猫"})


def make_service(api, identity_store):
    return GenerationService(
        context_collector=FakeContextCollector(),
        prompt_compiler=FakeCompiler(),
        identity_store=identity_store,
        image_cache=FakeImageCache(),
        images_api=api,
        bot_aliases=("你", "MaiBot"),
        identity_enabled=True,
    )


class GenerationServiceTests(IsolatedAsyncioTestCase):
    async def test_explicit_other_subject_wins_over_identity_hint(self):
        api = FakeImagesApi()
        service = make_service(api, FakeIdentityStore())
        request = GenerationRequest.from_mapping(
            {
                "request": "请生成一张猫的图片",
                "subject": "other",
                "use_bot_identity": True,
            }
        )

        try:
            outcome = await service.generate("stream-1", "stream-1", request)
        except IdentityRequiredError as exc:
            self.fail(f"explicit subject did not win over identity hint: {exc}")

        self.assertIs(outcome.plan.subject, Subject.OTHER)
        self.assertIs(outcome.plan.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertFalse(outcome.used_bot_identity)

    async def test_auto_polite_addressee_generates_other_without_identity(self):
        api = FakeImagesApi()
        service = make_service(api, FakeIdentityStore())
        request = GenerationRequest.from_mapping(
            {"request": "请你帮我生成一张猫的图片"}
        )

        try:
            outcome = await service.generate("stream-1", "stream-1", request)
        except IdentityRequiredError as exc:
            self.fail(f"polite addressee was treated as Bot subject: {exc}")

        self.assertIs(outcome.plan.subject, Subject.OTHER)
        self.assertIs(outcome.plan.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertFalse(outcome.used_bot_identity)
        self.assertEqual(len(api.calls), 1)

    async def test_auto_location_addressee_generates_other_without_identity(self):
        api = FakeImagesApi()
        service = make_service(api, FakeIdentityStore())
        request = GenerationRequest.from_mapping(
            {"request": "请你在海边生成一张猫的照片"}
        )

        try:
            outcome = await service.generate("stream-1", "stream-1", request)
        except IdentityRequiredError as exc:
            self.fail(f"location addressee was treated as Bot subject: {exc}")

        self.assertIs(outcome.plan.subject, Subject.OTHER)
        self.assertIs(outcome.plan.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertFalse(outcome.used_bot_identity)
        self.assertEqual(len(api.calls), 1)

    async def test_auto_character_name_uses_fixed_identity(self):
        api = FakeImagesApi()
        service = GenerationService(
            context_collector=FakeContextCollector(),
            prompt_compiler=FakeCompiler(),
            identity_store=FakeIdentityStore(reference()),
            image_cache=FakeImageCache(),
            images_api=api,
            bot_aliases=(),
            identity_enabled=True,
            character_name="小麦",
        )
        request = GenerationRequest.from_mapping(
            {"request": "生成小麦在海边的照片"}
        )

        outcome = await service.generate("stream-1", "stream-1", request)

        self.assertIs(outcome.plan.subject, Subject.BOT)
        self.assertIs(outcome.plan.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertTrue(outcome.used_bot_identity)

    async def test_explicit_edit_without_base_stops_before_context_or_api(self):
        collector = FakeContextCollector()
        compiler = FakeCompiler()
        api = FakeImagesApi()
        service = GenerationService(
            context_collector=collector,
            prompt_compiler=compiler,
            identity_store=FakeIdentityStore(reference()),
            image_cache=FakeImageCache(),
            images_api=api,
            bot_aliases=("你", "MaiBot"),
            identity_enabled=True,
        )
        request = GenerationRequest.from_mapping(
            {"request": "改成下雪的夜晚", "mode": "image_to_image"}
        )

        with self.assertRaises(ImageSourceRequiredError):
            await service.generate("stream-1", "stream-1", request)

        self.assertEqual(collector.calls, [])
        self.assertEqual(compiler.calls, [])
        self.assertEqual(api.calls, [])

    async def test_character_name_reaches_bot_identity_prompt(self):
        api = FakeImagesApi()
        service = GenerationService(
            context_collector=FakeContextCollector(),
            prompt_compiler=FakeCompiler(),
            identity_store=FakeIdentityStore(reference()),
            image_cache=FakeImageCache(),
            images_api=api,
            bot_aliases=("你", "MaiBot"),
            identity_enabled=True,
            character_name="小麦",
            reference_policy="fixed_only",
        )

        outcome = await service.generate("stream-1", "stream-1", bot_request())

        self.assertIn("固定身份角色：小麦", outcome.plan.final_prompt)

    def test_rejects_non_fixed_only_reference_policy(self):
        with self.assertRaises(ConfigurationError):
            GenerationService(
                context_collector=FakeContextCollector(),
                prompt_compiler=FakeCompiler(),
                identity_store=FakeIdentityStore(reference()),
                image_cache=FakeImageCache(),
                images_api=FakeImagesApi(),
                bot_aliases=("你", "MaiBot"),
                identity_enabled=True,
                character_name="小麦",
                reference_policy="recent_first",
            )

    async def test_bot_request_missing_identity_never_calls_api(self):
        api = FakeImagesApi()
        service = make_service(api, FakeIdentityStore())

        with self.assertRaises(IdentityRequiredError):
            await service.generate("stream-1", "stream-1", bot_request())

        self.assertEqual(api.calls, [])

    async def test_bot_request_builds_identity_edit_plan(self):
        api = FakeImagesApi()
        service = make_service(api, FakeIdentityStore(reference()))

        outcome = await service.generate("stream-1", "stream-1", bot_request())

        self.assertEqual(len(api.calls), 1)
        self.assertIs(outcome.plan.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertEqual(outcome.plan.references[0].image_id, "identity")
        self.assertTrue(outcome.used_bot_identity)

    async def test_new_bot_scene_never_chains_recent_generated_image(self):
        api = FakeImagesApi()
        service = GenerationService(
            context_collector=FakeContextCollector(),
            prompt_compiler=FakeCompiler(),
            identity_store=FakeIdentityStore(reference()),
            image_cache=FakeImageCache(
                reference("old-generated", ReferenceRole.BASE)
            ),
            images_api=api,
            bot_aliases=("你", "MaiBot"),
            identity_enabled=True,
            character_name="麦麦",
            reference_policy="fixed_only",
        )
        request = GenerationRequest.from_mapping(
            {"request": "我想看你穿汉服的样子"}
        )

        outcome = await service.generate("stream-1", "stream-1", request)

        self.assertEqual(
            [item.role for item in outcome.plan.references],
            [ReferenceRole.IDENTITY],
        )
        self.assertNotIn(
            "old-generated",
            [item.image_id for item in outcome.plan.references],
        )

    async def test_success_updates_previous_prompt_only_after_api_returns(self):
        api = FakeImagesApi()
        service = make_service(api, FakeIdentityStore(reference()))

        outcome = await service.generate("stream-1", "stream-1", bot_request())

        self.assertEqual(
            service.previous_prompt("stream-1"),
            outcome.plan.final_prompt,
        )


class DeliveryTests(IsolatedAsyncioTestCase):
    async def test_success_sends_exactly_once_and_returns_media(self):
        sender = FakeSender()
        delivery = Delivery(sender, include_tool_media=True)

        result = await delivery.send(
            GeneratedImage(VALID_PNG, "image/png", "images"),
            "stream-1",
            "final prompt",
            mode=GenerationMode.IDENTITY_REFERENCE,
            used_context=True,
            used_bot_identity=True,
        )

        self.assertEqual(len(sender.images), 1)
        self.assertEqual(
            base64.b64decode(sender.images[0][0]),
            VALID_PNG,
        )
        self.assertTrue(result["already_sent"])
        self.assertEqual(result["content_items"][0]["mime_type"], "image/png")


class FakeService:
    def __init__(self):
        self.call_count = 0

    def base_image_id(self, stream_id):
        return ""

    async def generate(self, stream_id, chat_id, request):
        self.call_count += 1
        plan = GenerationPlan(
            request=request,
            mode=GenerationMode.TEXT_TO_IMAGE,
            subject=Subject.OTHER,
            final_prompt=request.request,
            references=(),
            operation="generation",
        )
        return GenerationOutcome(
            GeneratedImage(VALID_PNG, "image/png", "images"),
            plan,
            request.use_context,
            False,
        )


class BlockingService(FakeService):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, stream_id, chat_id, request):
        self.entered.set()
        await self.release.wait()
        return await super().generate(stream_id, chat_id, request)


class RuntimeTests(IsolatedAsyncioTestCase):
    async def test_generation_gate_runs_after_generate_and_before_send(self):
        events = []

        class EventService(FakeService):
            async def generate(self, stream_id, chat_id, request):
                events.append("generate")
                return await super().generate(stream_id, chat_id, request)

        class EventDelivery:
            async def send(self, *args, **kwargs):
                events.append("send")
                return {"success": True}

        async def gate():
            events.append("gate")

        runtime = PluginRuntime(EventService(), EventDelivery())

        result = await runtime.generate(
            "stream-1",
            "chat-1",
            cat_request(),
            before_delivery=gate,
        )

        self.assertTrue(result["success"])
        self.assertEqual(events, ["generate", "gate", "send"])

    async def test_generation_failure_does_not_run_delivery_gate(self):
        gate_calls = []

        class FailingService(FakeService):
            async def generate(self, stream_id, chat_id, request):
                raise RuntimeError("generation failed")

        async def gate():
            gate_calls.append(True)

        runtime = PluginRuntime(FailingService(), Delivery(FakeSender()))

        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            await runtime.generate(
                "stream-1",
                "chat-1",
                cat_request(),
                before_delivery=gate,
            )

        self.assertEqual(gate_calls, [])

    async def test_has_recent_image_reports_cache_state_without_adding_items(self):
        cache = RecentImageCache(8, 900)
        runtime = PluginRuntime(
            FakeService(),
            Delivery(FakeSender()),
            image_cache=cache,
        )

        self.assertFalse(runtime.has_recent_image("stream-1"))
        cache.put("stream-1", reference("recent", ReferenceRole.BASE))
        before = cache.items("stream-1")

        self.assertTrue(runtime.has_recent_image("stream-1"))
        self.assertEqual(cache.items("stream-1"), before)

    async def test_has_recent_image_does_not_prune_expired_cache_storage(self):
        now = [100.0]
        cache = RecentImageCache(8, 10, clock=lambda: now[0])
        cache.put(
            "stream-1",
            ImageReference(
                image_id="expired",
                data=VALID_PNG,
                mime_type="image/png",
                source="expired",
                role=ReferenceRole.BASE,
                timestamp=80.0,
            ),
        )
        stored = cache._items["stream-1"]
        runtime = PluginRuntime(
            FakeService(),
            Delivery(FakeSender()),
            image_cache=cache,
        )

        self.assertFalse(runtime.has_recent_image("stream-1"))
        self.assertEqual(len(stored), 1)
        self.assertIs(cache._items.get("stream-1"), stored)

    async def test_service_fallback_recent_query_does_not_prune_expired_storage(self):
        now = [100.0]
        cache = RecentImageCache(8, 10, clock=lambda: now[0])
        cache.put("stream-1", reference("recent", ReferenceRole.BASE))
        stored = cache._items["stream-1"]
        service = GenerationService(
            context_collector=FakeContextCollector(),
            prompt_compiler=FakeCompiler(),
            identity_store=FakeIdentityStore(reference()),
            image_cache=cache,
            images_api=FakeImagesApi(),
            bot_aliases=("你", "MaiBot"),
            identity_enabled=True,
        )
        runtime = PluginRuntime(service, Delivery(FakeSender()))

        self.assertTrue(service.has_recent_image("stream-1"))
        self.assertTrue(runtime.has_recent_image("stream-1"))
        now[0] = 111.0

        self.assertFalse(service.has_recent_image("stream-1"))
        self.assertFalse(runtime.has_recent_image("stream-1"))
        self.assertEqual(len(stored), 1)
        self.assertIs(cache._items.get("stream-1"), stored)

    async def test_message_image_data_url_enters_recent_cache(self):
        cache = RecentImageCache(8, 900)
        with TemporaryDirectory() as directory:
            runtime = PluginRuntime(
                FakeService(),
                Delivery(FakeSender()),
                image_cache=cache,
                identity_store=IdentityStore(Path(directory), "bot-face.png", 1024),
            )
            encoded = base64.b64encode(VALID_PNG).decode("ascii")

            count = runtime.cache_message_image(
                "stream-1",
                {
                    "message_segments": [
                        {"type": "image", "data": f"data:image/png;base64,{encoded}"}
                    ]
                },
            )

        self.assertEqual(count, 1)
        self.assertEqual(cache.latest("stream-1").data, VALID_PNG)

    async def test_successful_duplicate_does_not_call_api_or_send_twice(self):
        service = FakeService()
        sender = FakeSender()
        runtime = PluginRuntime(
            service,
            Delivery(sender),
            max_concurrency=2,
            skip_when_busy=True,
            dedupe_seconds=90,
        )

        first = await runtime.generate("stream-1", "stream-1", cat_request())
        second = await runtime.generate("stream-1", "stream-1", cat_request())

        self.assertTrue(first["success"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(service.call_count, 1)
        self.assertEqual(len(sender.images), 1)

    async def test_skip_when_busy_rejects_second_same_stream_request(self):
        service = BlockingService()
        runtime = PluginRuntime(
            service,
            Delivery(FakeSender()),
            max_concurrency=2,
            skip_when_busy=True,
            dedupe_seconds=90,
        )
        first = asyncio.create_task(
            runtime.generate("stream-1", "stream-1", cat_request())
        )
        await service.entered.wait()

        with self.assertRaises(BusyError):
            await runtime.generate("stream-1", "stream-1", cat_request())

        service.release.set()
        await first
