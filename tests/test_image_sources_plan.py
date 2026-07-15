from unittest import TestCase

from context_image.errors import IdentityRequiredError, ImageSourceRequiredError
from context_image.image_sources import RecentImageCache, build_generation_plan
from context_image.models import (
    CompiledPrompt,
    GenerationMode,
    GenerationRequest,
    ImageReference,
    ReferenceRole,
    Subject,
)
from context_image.prompt_assembler import assemble_prompt


def image_ref(
    image_id: str,
    role: ReferenceRole,
    *,
    timestamp: float = 100.0,
) -> ImageReference:
    return ImageReference(
        image_id=image_id,
        data=b"\x89PNG\r\n\x1a\n" + image_id.encode(),
        mime_type="image/png",
        source=image_id,
        role=role,
        timestamp=timestamp,
    )


class PromptAssemblerTests(TestCase):
    def test_bot_identity_contract_names_the_fixed_character(self):
        prompt = assemble_prompt(
            CompiledPrompt(subject=Subject.BOT),
            GenerationRequest.from_mapping(
                {"request": "穿汉服站在庭院里", "subject": "bot"}
            ),
            Subject.BOT,
            character_name="小麦",
        )

        self.assertIn("固定身份角色：小麦", prompt)

    def test_bot_identity_contract_precedes_current_request_and_context(self):
        request = GenerationRequest.from_mapping(
            {
                "request": "换成雨夜窗边自拍",
                "subject": "bot",
                "prompt_fragments": ["手机前置镜头"],
            }
        )
        compiled = CompiledPrompt(
            subject=Subject.BOT,
            scene="旧场景：晴天海边",
            style="写实生活摄影",
            must_preserve=("固定身份图中的面部特征",),
        )

        prompt = assemble_prompt(compiled, request, Subject.BOT)

        self.assertLess(prompt.index("规范外貌"), prompt.index("换成雨夜窗边自拍"))
        self.assertLess(prompt.index("换成雨夜窗边自拍"), prompt.index("旧场景"))
        self.assertIn("手机前置镜头", prompt)
        self.assertIn("不要重新设计人物面孔", prompt)

    def test_override_keeps_identity_but_drops_compiled_scene(self):
        request = GenerationRequest.from_mapping(
            {
                "request": "在书房看书",
                "subject": "bot",
                "prompt_join_mode": "override",
            }
        )

        prompt = assemble_prompt(
            CompiledPrompt(scene="旧场景", subject=Subject.BOT),
            request,
            Subject.BOT,
        )

        self.assertIn("规范外貌", prompt)
        self.assertIn("在书房看书", prompt)
        self.assertNotIn("旧场景", prompt)


class GenerationPlanTests(TestCase):
    def test_explicit_image_to_image_plan_rejects_missing_base(self):
        with self.assertRaises(ImageSourceRequiredError):
            build_generation_plan(
                GenerationRequest.from_mapping(
                    {"request": "改成雪夜", "mode": "image_to_image"}
                ),
                CompiledPrompt(scene="雪夜"),
                None,
                None,
                ("你",),
            )

    def test_bot_edit_orders_identity_before_base(self):
        plan = build_generation_plan(
            request=GenerationRequest.from_mapping(
                {"request": "保持人物，改成下雪夜晚", "subject": "bot"}
            ),
            compiled=CompiledPrompt(scene="下雪夜晚", subject=Subject.BOT),
            base_image=image_ref("base", ReferenceRole.BASE),
            identity_image=image_ref("identity", ReferenceRole.IDENTITY),
            aliases=("你", "MaiBot"),
            character_name="小麦",
        )

        self.assertEqual(
            [item.role for item in plan.references],
            [ReferenceRole.IDENTITY, ReferenceRole.BASE],
        )
        self.assertIn("固定身份角色：小麦", plan.final_prompt)
        self.assertIn("第一张固定身份图", plan.final_prompt)
        self.assertEqual(plan.operation, "edit")
        self.assertIs(plan.mode, GenerationMode.IMAGE_TO_IMAGE)

    def test_non_bot_without_base_uses_generation(self):
        plan = build_generation_plan(
            GenerationRequest.from_mapping({"request": "画一只月球橘猫"}),
            CompiledPrompt(scene="月球上的橘猫"),
            None,
            None,
            ("你",),
        )

        self.assertEqual(plan.operation, "generation")
        self.assertEqual(plan.references, ())
        self.assertIs(plan.mode, GenerationMode.TEXT_TO_IMAGE)

    def test_bot_plan_rejects_missing_identity(self):
        with self.assertRaises(IdentityRequiredError):
            build_generation_plan(
                GenerationRequest.from_mapping(
                    {"request": "拍张自拍", "subject": "bot"}
                ),
                CompiledPrompt(scene="自拍", subject=Subject.BOT),
                None,
                None,
                ("你",),
            )


class RecentImageCacheTests(TestCase):
    def test_latest_returns_newest_non_expired_image(self):
        now = [100.0]
        cache = RecentImageCache(2, 10, clock=lambda: now[0])
        cache.put("stream", image_ref("old", ReferenceRole.BASE, timestamp=95))
        cache.put("stream", image_ref("new", ReferenceRole.BASE, timestamp=99))

        self.assertEqual(cache.latest("stream").image_id, "new")
        now[0] = 111.0
        self.assertIsNone(cache.latest("stream"))

    def test_cache_enforces_per_stream_limit(self):
        cache = RecentImageCache(2, 1000, clock=lambda: 100.0)
        cache.put("stream", image_ref("one", ReferenceRole.BASE))
        cache.put("stream", image_ref("two", ReferenceRole.BASE))
        cache.put("stream", image_ref("three", ReferenceRole.BASE))

        self.assertEqual(
            [item.image_id for item in cache.items("stream")],
            ["two", "three"],
        )
