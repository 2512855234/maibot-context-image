import asyncio
import json
from dataclasses import FrozenInstanceError
from unittest import IsolatedAsyncioTestCase, TestCase

from context_image.config import ContextImageConfig
from context_image.intent_detector import IntentDetector
from context_image.message_parser import (
    TriggerMessage,
    is_private_message,
    parse_trigger_message,
)
from context_image.models import CaptureType, GenerationMode, Subject
from context_image.reply_gate import ReplyGate
from tests.fakes import (
    ClockAdvancingMessageProxy,
    FakeLLM,
    NeverReturningMessageProxy,
    SequencedMessageProxy,
)


def planner_response(**overrides) -> str:
    payload = {
        "triggered": True,
        "confidence": 0.91,
        "intent": "generate",
        "subject": "bot",
        "capture_type": "full_body",
        "mode": "identity_reference",
        "normalized_request": "生成一张你穿汉服的全身照",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def private_message(**overrides):
    message = {
        "message_id": "message-1",
        "timestamp": 100.5,
        "session_id": "session-1",
        "processed_plain_text": "我想看你穿汉服的样子",
        "message_info": {
            "user_info": {"user_id": "user-1"},
            "group_info": None,
        },
        "is_command": False,
        "is_notify": False,
        "raw_message": [],
    }
    message.update(overrides)
    return message


def recent_message(message_id, timestamp, user_id):
    return {
        "message_id": message_id,
        "timestamp": timestamp,
        "message_info": {"user_info": {"user_id": user_id}},
    }


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class MessageParserTests(TestCase):
    def test_private_guard_accepts_image_only_private_record(self):
        message = private_message(
            processed_plain_text="",
            raw_message=[{"type": "image", "data": "image-data"}],
        )

        self.assertTrue(is_private_message(message))
        self.assertIsNone(parse_trigger_message(message, "stream-1"))

    def test_private_guard_rejects_group_and_non_dictionary_records(self):
        group = private_message(
            message_info={
                "user_info": {"user_id": "user-1"},
                "group_info": {"group_id": "group-1"},
            }
        )

        self.assertFalse(is_private_message(group))
        self.assertFalse(is_private_message(None))
        self.assertFalse(is_private_message([group]))
        self.assertFalse(is_private_message({"message_info": None}))

    def test_parser_builds_immutable_slotted_trigger_from_processed_text(self):
        parsed = parse_trigger_message(private_message(), " stream-1 ")

        self.assertEqual(
            parsed,
            TriggerMessage(
                "message-1",
                "stream-1",
                "user-1",
                "我想看你穿汉服的样子",
                100.5,
            ),
        )
        self.assertFalse(hasattr(parsed, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            parsed.text = "changed"

    def test_parser_prefers_processed_text_over_raw_segments(self):
        message = private_message(
            processed_plain_text="处理后的文本",
            raw_message=[{"type": "text", "data": "原始文本"}],
        )

        parsed = parse_trigger_message(message, "stream-1")

        self.assertEqual(parsed.text, "处理后的文本")

    def test_parser_concatenates_only_text_segments_from_raw_message(self):
        message = private_message(
            processed_plain_text="",
            raw_message=[
                {"type": "text", "data": " 第一段 "},
                {"type": "image", "data": "ignored-image"},
                {"type": "text", "data": "第二段"},
                "malformed",
            ],
        )

        parsed = parse_trigger_message(message, "stream-1")

        self.assertEqual(parsed.text, "第一段 第二段")

    def test_parser_uses_message_segments_when_raw_message_is_absent(self):
        message = private_message(
            processed_plain_text="",
            raw_message=None,
            message_segments=[{"type": "text", "data": "备用文本"}],
        )

        parsed = parse_trigger_message(message, "stream-1")

        self.assertEqual(parsed.text, "备用文本")

    def test_parser_rejects_command_notify_empty_text_and_missing_identifiers(self):
        invalid_messages = (
            private_message(is_command=True),
            private_message(is_notify=True),
            private_message(processed_plain_text="   "),
            private_message(message_id="   "),
            private_message(message_info={"group_info": None}),
            private_message(
                message_info={"user_info": {}, "group_info": None}
            ),
        )

        for message in invalid_messages:
            with self.subTest(message=message):
                self.assertIsNone(parse_trigger_message(message, "stream-1"))
        self.assertIsNone(parse_trigger_message(private_message(), "   "))

    def test_parser_rejects_group_records(self):
        message = private_message(
            message_info={
                "user_info": {"user_id": "user-1"},
                "group_info": {"group_id": "group-1"},
            }
        )

        self.assertIsNone(parse_trigger_message(message, "stream-1"))

    def test_parser_defaults_invalid_or_missing_timestamp_to_zero(self):
        invalid = parse_trigger_message(
            private_message(timestamp="not-a-timestamp"), "stream-1"
        )
        missing_message = private_message()
        missing_message.pop("timestamp")
        missing = parse_trigger_message(missing_message, "stream-1")

        self.assertEqual(invalid.timestamp, 0.0)
        self.assertEqual(missing.timestamp, 0.0)


class ReplyGateTests(IsolatedAsyncioTestCase):
    async def test_returns_true_for_later_message_from_different_sender(self):
        proxy = SequencedMessageProxy(
            [
                [recent_message("source-1", 100, "user-1")],
                [
                    recent_message("source-1", 100, "user-1"),
                    recent_message("reply-1", 101, "bot-1"),
                ],
            ]
        )
        fake_time = FakeTime()
        gate = ReplyGate(
            proxy,
            timeout_seconds=2,
            poll_seconds=0.5,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )

        result = await gate.wait("chat-1", "source-1", "user-1", 100)

        self.assertTrue(result)
        self.assertEqual(proxy.recent_calls, [("chat-1", 8), ("chat-1", 8)])
        self.assertEqual(fake_time.sleeps, [0.5])

    async def test_zero_second_timeout_rejects_already_eligible_reply(self):
        proxy = SequencedMessageProxy(
            [[recent_message("reply-1", 101, "bot-1")]]
        )
        fake_time = FakeTime()
        gate = ReplyGate(
            proxy,
            timeout_seconds=0,
            poll_seconds=0.5,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )

        result = await gate.wait("chat-1", "source-1", "user-1", 100)

        self.assertFalse(result)
        self.assertEqual(proxy.recent_calls, [])
        self.assertEqual(fake_time.sleeps, [])

    async def test_reply_returned_after_deadline_is_rejected(self):
        fake_time = FakeTime()
        proxy = ClockAdvancingMessageProxy(
            fake_time,
            0.6,
            [recent_message("reply-1", 101, "bot-1")],
        )
        gate = ReplyGate(
            proxy,
            timeout_seconds=0.5,
            poll_seconds=0.25,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )

        result = await gate.wait("chat-1", "source-1", "user-1", 100)

        self.assertFalse(result)
        self.assertEqual(proxy.recent_calls, [("chat-1", 8)])
        self.assertEqual(fake_time.sleeps, [])

    async def test_never_returning_proxy_is_bounded_by_gate_timeout(self):
        proxy = NeverReturningMessageProxy()
        gate = ReplyGate(proxy, timeout_seconds=0.02, poll_seconds=0.005)

        try:
            result = await asyncio.wait_for(
                gate.wait("chat-1", "source-1", "user-1", 100),
                timeout=0.2,
            )
        except TimeoutError:
            result = "gate-hung"

        self.assertFalse(result)
        self.assertTrue(proxy.cancelled)

    async def test_ignores_same_source_user_older_and_malformed_records(self):
        records = [
            None,
            "malformed",
            {},
            recent_message("source-1", 101, "bot-1"),
            recent_message("reply-same-user", 102, "user-1"),
            recent_message("reply-older", 99, "bot-1"),
            recent_message("reply-invalid-time", "invalid", "bot-1"),
            {
                "message_id": "reply-missing-user",
                "timestamp": 103,
                "message_info": {"user_info": {}},
            },
        ]
        proxy = SequencedMessageProxy([records])
        fake_time = FakeTime()
        gate = ReplyGate(
            proxy,
            timeout_seconds=0,
            poll_seconds=0.5,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )

        result = await gate.wait("chat-1", "source-1", "user-1", 100)

        self.assertFalse(result)

    async def test_proxy_error_is_transient_until_later_reply_arrives(self):
        proxy = SequencedMessageProxy(
            [
                RuntimeError("temporary read failure"),
                [recent_message("reply-1", 101, "bot-1")],
            ]
        )
        fake_time = FakeTime()
        gate = ReplyGate(
            proxy,
            timeout_seconds=1,
            poll_seconds=0.25,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )

        result = await gate.wait("chat-1", "source-1", "user-1", 100)

        self.assertTrue(result)
        self.assertEqual(fake_time.sleeps, [0.25])

    async def test_repeated_errors_end_at_timeout(self):
        proxy = SequencedMessageProxy(
            [RuntimeError("first"), RuntimeError("second"), RuntimeError("third")]
        )
        fake_time = FakeTime()
        gate = ReplyGate(
            proxy,
            timeout_seconds=0.5,
            poll_seconds=0.25,
            monotonic=fake_time.monotonic,
            sleep=fake_time.sleep,
        )

        result = await gate.wait("chat-1", "source-1", "user-1", 100)

        self.assertFalse(result)
        self.assertEqual(fake_time.sleeps, [0.25, 0.25])


class IntentDetectorTests(IsolatedAsyncioTestCase):
    async def test_explicit_selfie_is_deterministic(self):
        llm = FakeLLM("")
        detector = IntentDetector(llm)

        decision = await detector.detect("给我拍一张你现在的照片")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.BOT)
        self.assertIs(decision.capture_type, CaptureType.SELFIE)
        self.assertIs(decision.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertEqual(llm.calls, [])

    async def test_photo_request_without_polite_prefix_is_deterministic(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("拍一张你现在的照片")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.BOT)
        self.assertIs(decision.capture_type, CaptureType.SELFIE)
        self.assertEqual(llm.calls, [])

    async def test_explicit_outfit_request_is_deterministic(self):
        llm = FakeLLM("")
        detector = IntentDetector(llm)

        decision = await detector.detect("我想看你穿汉服的样子")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.BOT)
        self.assertIs(decision.capture_type, CaptureType.FULL_BODY)
        self.assertIs(decision.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertEqual(llm.calls, [])

    async def test_custom_bot_alias_resolves_outfit_request_to_identity(self):
        llm = FakeLLM("")
        detector = IntentDetector(
            llm,
            bot_aliases=[" 麦麦 "],
            character_name=" 麦麦 ",
        )

        decision = await detector.detect("我想看麦麦穿汉服的样子")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.BOT)
        self.assertIs(decision.capture_type, CaptureType.FULL_BODY)
        self.assertIs(decision.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertEqual(llm.calls, [])

    async def test_custom_character_name_resolves_photo_request_to_identity(self):
        llm = FakeLLM("")
        detector = IntentDetector(llm, character_name=" 麦麦 ")

        decision = await detector.detect("拍一张麦麦现在的照片")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.BOT)
        self.assertIs(decision.capture_type, CaptureType.SELFIE)
        self.assertIs(decision.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertEqual(llm.calls, [])

    async def test_latin_alias_does_not_match_inside_larger_token(self):
        llm = FakeLLM("")
        detector = IntentDetector(llm, bot_aliases=["bot"])

        decision = await detector.detect("请生成一张robot的海报")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.OTHER)
        self.assertIs(decision.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertEqual(llm.calls, [])

    async def test_cjk_alias_does_not_match_inside_larger_name(self):
        llm = FakeLLM("")
        detector = IntentDetector(llm, bot_aliases=["小麦"])

        decision = await detector.detect("生成小麦田的风景照片")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.OTHER)
        self.assertIs(decision.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertEqual(llm.calls, [])

    async def test_reported_image_request_fails_closed_before_planner(self):
        llm = FakeLLM(planner_response())
        detector = IntentDetector(llm)

        decisions = (
            await detector.detect("他刚才说：给我拍一张你现在的照片"),
            await detector.detect("朋友说：“请你帮我生成一张猫的图片”"),
            await detector.detect("她提到过：我想看你穿汉服的样子"),
        )

        self.assertTrue(all(not decision.triggered for decision in decisions))
        self.assertEqual(llm.calls, [])

    async def test_first_person_discourse_image_requests_remain_direct(self):
        llm = FakeLLM(planner_response())
        detector = IntentDetector(llm)

        decisions = (
            await detector.detect("我想说：给我拍一张你现在的照片"),
            await detector.detect("我跟你说：给我拍一张你现在的照片"),
        )

        self.assertTrue(all(decision.triggered for decision in decisions))
        self.assertTrue(all(decision.subject is Subject.BOT for decision in decisions))
        self.assertEqual(llm.calls, [])

    async def test_negative_request_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("不要生成图片")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_capability_question_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("你会拍照吗？")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_regular_discussion_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("刚才那张照片很好看")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_general_planning_question_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("我们今天做什么好？")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_project_retro_discussion_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("给项目做个复盘")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_generative_ai_discussion_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("生成式 AI 很有趣")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_image_generation_capability_question_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("你会生成图片吗？")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_photo_how_to_question_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("怎么拍一张好看的照片？")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_photo_cost_question_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("拍一张照片要多少钱？")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_image_duration_question_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("生成一张图片通常要多久？")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_outfit_advice_question_is_not_triggered(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("我想看看汉服应该怎么穿")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_cost_term_inside_image_subject_still_triggers(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("画一张成本构成图")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.OTHER)
        self.assertIs(decision.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertEqual(llm.calls, [])

    async def test_how_to_term_inside_image_subject_still_triggers(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect(
            "生成一张展示如何穿汉服的图片"
        )

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.OTHER)
        self.assertIs(decision.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertEqual(llm.calls, [])

    async def test_want_prefix_image_request_is_deterministic(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("我想生成一张猫的图片")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.OTHER)
        self.assertIs(decision.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertEqual(llm.calls, [])

    async def test_polite_help_prefix_image_request_is_deterministic(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("请你帮我生成一张猫的图片")

        self.assertTrue(decision.triggered)
        self.assertIs(decision.subject, Subject.OTHER)
        self.assertIs(decision.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertEqual(llm.calls, [])

    async def test_default_pronoun_alias_requires_bot_subject_context(self):
        config = ContextImageConfig()
        llm = FakeLLM("")
        detector = IntentDetector(
            llm,
            bot_aliases=config.identity.bot_aliases,
            character_name=config.identity.character_name,
        )

        other = await detector.detect("请你帮我生成一张猫的图片")
        selfie = await detector.detect("给我拍一张你现在的照片")
        outfit = await detector.detect("我想看你穿汉服的样子")

        self.assertIs(other.subject, Subject.OTHER)
        self.assertIs(other.mode, GenerationMode.TEXT_TO_IMAGE)
        self.assertIs(selfie.subject, Subject.BOT)
        self.assertIs(selfie.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertIs(outfit.subject, Subject.BOT)
        self.assertIs(outfit.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertEqual(llm.calls, [])

    async def test_recent_image_edit_uses_image_to_image(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect(
            "把刚才那张图改成下雪", has_recent_image=True
        )

        self.assertTrue(decision.triggered)
        self.assertEqual(decision.intent, "edit")
        self.assertIs(decision.mode, GenerationMode.IMAGE_TO_IMAGE)
        self.assertEqual(llm.calls, [])

    async def test_edit_without_recent_image_fails_closed(self):
        llm = FakeLLM("")

        decision = await IntentDetector(llm).detect("把刚才那张图改成下雪")

        self.assertFalse(decision.triggered)
        self.assertEqual(llm.calls, [])

    async def test_ambiguous_visual_candidate_uses_planner(self):
        llm = FakeLLM(planner_response())

        decision = await IntentDetector(llm).detect("如果你穿汉服会是什么样？")

        self.assertTrue(decision.triggered)
        self.assertEqual(decision.confidence, 0.91)
        self.assertIs(decision.subject, Subject.BOT)
        self.assertIs(decision.capture_type, CaptureType.FULL_BODY)
        self.assertIs(decision.mode, GenerationMode.IDENTITY_REFERENCE)
        self.assertEqual(len(llm.calls), 1)
        _, kwargs = llm.calls[0]
        self.assertEqual(kwargs["model"], "planner")
        self.assertEqual(kwargs["temperature"], 0)
        self.assertEqual(kwargs["max_tokens"], 400)

    async def test_ambiguous_request_sends_normalized_identity_to_planner(self):
        llm = FakeLLM(planner_response())
        detector = IntentDetector(
            llm,
            bot_aliases=[" 小麦 ", "MAIBOT", "小麦"],
            character_name=" 麦麦 ",
        )

        decision = await detector.detect("如果麦麦穿汉服会是什么样？")

        self.assertTrue(decision.triggered)
        messages, _ = llm.calls[0]
        planner_input = json.loads(messages[1]["content"])
        self.assertEqual(planner_input["character_name"], "麦麦")
        self.assertEqual(planner_input["bot_aliases"], ["小麦", "maibot", "麦麦"])

    async def test_invalid_planner_json_fails_closed(self):
        llm = FakeLLM("not json")

        decision = await IntentDetector(llm).detect("如果你穿汉服会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_confidence_below_threshold_fails_closed(self):
        llm = FakeLLM(planner_response(confidence=0.79))

        decision = await IntentDetector(llm).detect("如果你穿汉服会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_missing_required_planner_field_fails_closed(self):
        payload = json.loads(planner_response())
        del payload["normalized_request"]
        llm = FakeLLM(json.dumps(payload, ensure_ascii=False))

        decision = await IntentDetector(llm).detect("如果你穿汉服会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_invalid_planner_enum_fails_closed(self):
        llm = FakeLLM(planner_response(mode="unsupported"))

        decision = await IntentDetector(llm).detect("如果你穿汉服会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_triggered_none_intent_fails_closed(self):
        llm = FakeLLM(planner_response(intent="none"))

        decision = await IntentDetector(llm).detect("如果你穿汉服会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_edit_with_text_to_image_mode_fails_closed(self):
        llm = FakeLLM(
            planner_response(intent="edit", mode="text_to_image")
        )

        decision = await IntentDetector(llm).detect(
            "如果把照片画成雪景会是什么样？",
            has_recent_image=True,
        )

        self.assertFalse(decision.triggered)

    async def test_non_bot_identity_reference_fails_closed(self):
        llm = FakeLLM(
            planner_response(subject="other", mode="identity_reference")
        )

        decision = await IntentDetector(llm).detect("如果画成头像会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_image_to_image_without_recent_image_fails_closed(self):
        llm = FakeLLM(
            planner_response(intent="edit", mode="image_to_image")
        )

        decision = await IntentDetector(llm).detect("如果把照片画成雪景会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_planner_exception_fails_closed(self):
        llm = FakeLLM("", error=RuntimeError("planner unavailable"))

        decision = await IntentDetector(llm).detect("如果你穿汉服会是什么样？")

        self.assertFalse(decision.triggered)

    async def test_custom_planner_task_and_threshold_are_used(self):
        llm = FakeLLM(planner_response(confidence=0.85))
        detector = IntentDetector(
            llm,
            planner_model="visual-intent-planner",
            semantic_threshold=0.85,
        )

        decision = await detector.detect("如果你穿汉服会是什么样？")

        self.assertTrue(decision.triggered)
        _, kwargs = llm.calls[0]
        self.assertEqual(kwargs["model"], "visual-intent-planner")
