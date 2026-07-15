from dataclasses import FrozenInstanceError
from unittest import TestCase

from context_image.config import ContextImageConfig
from context_image.models import (
    CaptureType,
    GenerationMode,
    GenerationRequest,
    ImageIntentDecision,
    Subject,
)


class ModelTests(TestCase):
    def test_image_intent_decision_is_frozen(self):
        decision = ImageIntentDecision(False)

        with self.assertRaises(FrozenInstanceError):
            decision.triggered = True

    def test_image_intent_decision_uses_slots(self):
        decision = ImageIntentDecision(False)

        self.assertFalse(hasattr(decision, "__dict__"))

    def test_image_intent_decision_no_trigger_uses_safe_defaults(self):
        decision = ImageIntentDecision.no_trigger()

        self.assertFalse(decision.triggered)
        self.assertEqual(decision.confidence, 0.0)
        self.assertEqual(decision.intent, "none")
        self.assertIs(decision.subject, Subject.OTHER)
        self.assertIs(decision.capture_type, CaptureType.AUTO)
        self.assertIs(decision.mode, GenerationMode.AUTO)
        self.assertEqual(decision.normalized_request, "")

    def test_generation_request_normalizes_user_values(self):
        request = GenerationRequest.from_mapping(
            {
                "request": "  拍一张你现在的照片  ",
                "mode": "AUTO",
                "subject": "BOT",
                "prompt_fragments": [" warm light ", ""],
            }
        )

        self.assertEqual(request.request, "拍一张你现在的照片")
        self.assertIs(request.mode, GenerationMode.AUTO)
        self.assertIs(request.subject, Subject.BOT)
        self.assertEqual(request.prompt_fragments, ("warm light",))

    def test_generation_request_accepts_single_prompt_fragment(self):
        request = GenerationRequest.from_mapping(
            {"request": "画猫", "prompt_fragments": " cinematic light "}
        )

        self.assertEqual(request.prompt_fragments, ("cinematic light",))

    def test_generation_request_rejects_empty_text(self):
        with self.assertRaisesRegex(ValueError, "图片请求不能为空"):
            GenerationRequest.from_mapping({"request": "   "})

    def test_generation_request_rejects_unknown_join_mode(self):
        with self.assertRaisesRegex(ValueError, "不支持的提示词拼接模式"):
            GenerationRequest.from_mapping(
                {"request": "画猫", "prompt_join_mode": "unknown"}
            )


class ConfigTests(TestCase):
    def test_safe_defaults_match_mvp(self):
        config = ContextImageConfig()

        self.assertTrue(config.plugin.enabled)
        self.assertEqual(config.api.model, "gpt-image-2")
        self.assertEqual(config.api.api_key, "")
        self.assertEqual(config.api.api_key_env, "OPENAI_API_KEY")
        self.assertEqual(config.prompt.model, "planner")
        self.assertTrue(config.prompt.everyday_photo_defaults)
        self.assertIn("写实生活摄影", config.prompt.default_style)
        self.assertIn("自然光", config.prompt.default_lighting)
        self.assertIn("塑料皮肤", config.prompt.default_negative)
        self.assertTrue(config.identity.force_for_bot_images)
        self.assertEqual(config.identity.character_name, "麦麦")
        self.assertEqual(config.identity.reference_policy, "fixed_only")
        self.assertTrue(config.auto_trigger.enabled)
        self.assertEqual(config.auto_trigger.planner_model, "planner")
        self.assertEqual(config.auto_trigger.semantic_threshold, 0.80)
        self.assertEqual(config.auto_trigger.max_pending_per_chat, 2)
        self.assertEqual(config.auto_trigger.reply_wait_seconds, 12.0)
        self.assertEqual(config.auto_trigger.dedupe_seconds, 90.0)
        self.assertEqual(config.behavior.max_concurrency, 2)
        self.assertEqual(config.network.max_retries, 2)

        decision = ImageIntentDecision(
            True,
            1.0,
            "generate",
            Subject.BOT,
            CaptureType.FULL_BODY,
            GenerationMode.IDENTITY_REFERENCE,
            "我想看你穿汉服的样子",
        )
        self.assertIs(decision.subject, Subject.BOT)
        self.assertEqual(decision.normalized_request, "我想看你穿汉服的样子")
