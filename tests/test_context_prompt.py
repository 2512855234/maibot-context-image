from unittest import IsolatedAsyncioTestCase, TestCase

from context_image.context_collector import ContextCollector, sanitize_context
from context_image.models import ContextSnapshot, GenerationRequest, Subject
from context_image.prompt_compiler import PromptCompiler, extract_json_object
from tests.fakes import FakeLLM, FakeMessageProxy


class ContextCollectorTests(IsolatedAsyncioTestCase):
    async def test_collects_recent_messages_and_previous_prompt(self):
        proxy = FakeMessageProxy("Bot：正坐在窗边听雨")
        collector = ContextCollector(proxy, message_limit=16, max_chars=8000)

        result = await collector.collect(
            "chat-1",
            "拍张你现在的照片",
            previous_prompt="上一张是室内暖光",
        )

        self.assertEqual(proxy.recent_calls, [])
        self.assertEqual(
            proxy.readable_calls,
            [
                (
                    None,
                    {
                        "chat_id": "chat-1",
                        "limit": 16,
                        "replace_bot_name": True,
                        "timestamp_mode": "relative",
                        "truncate": False,
                    },
                )
            ],
        )
        self.assertIn("窗边听雨", result.recent_text)
        self.assertEqual(result.previous_prompt, "上一张是室内暖光")

    async def test_sanitizes_sensitive_and_binary_context(self):
        proxy = FakeMessageProxy(
            "参考 data:image/png;base64," + "A" * 300 + "\nAuthorization: Bearer sk-secret123"
        )
        collector = ContextCollector(proxy, message_limit=16, max_chars=120)

        result = await collector.collect("chat-1", "画图")

        self.assertNotIn("base64", result.recent_text)
        self.assertNotIn("sk-secret123", result.recent_text)
        self.assertLessEqual(len(result.recent_text), 120)


class JsonExtractionTests(TestCase):
    def test_extracts_json_from_markdown_fence(self):
        result = extract_json_object('```json\n{"scene":"雨夜窗边"}\n```')

        self.assertEqual(result["scene"], "雨夜窗边")

    def test_sanitize_context_removes_windows_paths(self):
        sanitized = sanitize_context(r"参考文件 D:\\private\\face.png，场景是窗边")

        self.assertNotIn("D:\\private", sanitized)
        self.assertIn("场景是窗边", sanitized)


class PromptCompilerTests(IsolatedAsyncioTestCase):
    async def test_default_everyday_photo_profile_is_merged(self):
        llm = FakeLLM('{"scene":"雨夜窗边","style":"真实手机摄影"}')
        compiler = PromptCompiler(llm)

        result = await compiler.compile(
            ContextSnapshot(current_request="拍张照片"),
            GenerationRequest.from_mapping({"request": "拍张照片"}),
            Subject.BOT,
        )

        self.assertIn("真实手机摄影", result.style)
        self.assertIn("普通手机或消费级相机", result.composition)
        self.assertIn("自然光或普通环境光", result.lighting)
        self.assertIn("塑料皮肤", result.negative)

    async def test_explicit_illustration_disables_everyday_photo_profile(self):
        llm = FakeLLM(
            '{"style":"水彩插画","use_default_photo_style":false}'
        )
        compiler = PromptCompiler(llm)

        result = await compiler.compile(
            ContextSnapshot(current_request="画成水彩插画"),
            GenerationRequest.from_mapping({"request": "画成水彩插画"}),
            Subject.OTHER,
        )

        self.assertEqual(result.style, "水彩插画")
        self.assertEqual(result.composition, "")
        self.assertNotIn("塑料皮肤", result.negative)
    async def test_context_enters_structured_prompt(self):
        llm = FakeLLM(
            '{"scene":"雨夜窗边","activity":"喝咖啡","style":"写实生活摄影"}'
        )
        compiler = PromptCompiler(
            llm,
            model="",
            temperature=0.2,
            max_tokens=1500,
        )

        result = await compiler.compile(
            ContextSnapshot(
                current_request="拍张照片",
                recent_text="Bot 正在窗边听雨",
            ),
            GenerationRequest.from_mapping({"request": "拍张照片"}),
            Subject.BOT,
        )

        self.assertEqual(result.scene, "雨夜窗边")
        self.assertEqual(result.activity, "喝咖啡")
        self.assertIn("固定身份图中的面部特征", result.must_preserve)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(len(llm.calls[0][0]), 2)

    async def test_invalid_json_falls_back_to_current_request(self):
        compiler = PromptCompiler(FakeLLM("not json"), fallback_to_template=True)

        result = await compiler.compile(
            ContextSnapshot(current_request="画一只月球橘猫", recent_text=""),
            GenerationRequest.from_mapping({"request": "画一只月球橘猫"}),
            Subject.OTHER,
        )

        self.assertEqual(result.scene, "画一只月球橘猫")
        self.assertNotIn("固定身份图中的面部特征", result.must_preserve)

    async def test_failed_llm_call_falls_back(self):
        compiler = PromptCompiler(
            FakeLLM("", success=False),
            fallback_to_template=True,
        )

        result = await compiler.compile(
            ContextSnapshot(current_request="发张自拍", recent_text="在书房看书"),
            GenerationRequest.from_mapping({"request": "发张自拍"}),
            Subject.BOT,
        )

        self.assertEqual(result.scene, "发张自拍")
        self.assertIn("写实生活摄影", result.style)
