"""Detect image-generation intent without turning every chat into an LLM call."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .models import (
    CaptureType,
    GenerationMode,
    ImageIntentDecision,
    Subject,
)
from .prompt_compiler import extract_json_object
from .subject_resolver import normalize_bot_aliases, resolve_text_subject


_NEGATIVE = re.compile(
    r"(?:不要|别|不需要|无需).{0,12}(?:生成|画|拍|做|发).{0,6}(?:图|照片|自拍|头像)"
)
_CAPABILITY = re.compile(
    r"(?:会不会|会|能不能|能).{0,6}"
    r"(?:画图|画画|拍照|生图|生成(?:图|图片|照片))(?:吗|么|？|\?)?$"
)
_HOW_TO_OR_ADVICE = re.compile(
    r"(?:^(?:怎么|如何)|(?:请问|想知道|应该|该).{0,4}(?:怎么|如何))"
    r".{0,24}(?:拍|画|生成|做|穿|戴|搭配)"
)
_COST_OR_DURATION = re.compile(
    r"(?:拍|画|生成|做).{0,40}"
    r"(?:(?:多少钱|多久|多长时间)|"
    r"(?:价格|费用|成本).{0,4}(?:是多少|多少|多高))(?:[？?])?$"
)
_PHOTO = re.compile(
    r"(?:给我|帮我|请)?(?:拍|发|来)(?:一|1)?张.{0,18}(?:照片|自拍|全身照|头像)"
)
_DRAW = re.compile(
    r"(?:^|[，。！？\s])"
    r"(?:我想|想要|请(?:你)?(?:帮我)?|麻烦(?:你)?(?:帮我)?|帮我|给我)?(?:"
    r"画(?:一|1)?(?:张|个|幅|只).{1,80}|"
    r"画.{0,36}(?:图片|图|照片|插画|海报|头像)|"
    r"生成(?:一|1)?(?:张|幅)?.{0,36}(?:图片|图|照片|插画|海报|头像)|"
    r"做(?:一|1)?(?:张|个|幅)(?:图|图片|插画|海报|头像)"
    r")"
)
_OUTFIT = re.compile(
    r"(?:想看|看看|让我看).{0,18}(?:穿|戴|换上|打扮成).{0,30}"
)
_EDIT = re.compile(
    r"(?:这张|这幅|刚才那张).{0,12}(?:改成|换成|变成|编辑|修改)"
)
_AMBIGUOUS = re.compile(
    r"(?:如果|假如|会是什么样|想象一下).{0,30}(?:穿|戴|拍|画|照片|样子)"
)
_REPORTED_OR_QUOTED_REQUEST = re.compile(
    r"^(?:(?:他|她|它|他们|她们|朋友|同事|老师|用户|别人|对方|"
    r"(?!(?:我|咱|本人))[\u4e00-\u9fff]{2,4}).{0,8}?"
    r"(?:说|问|提到|写道|发来|告诉我|要求|喊)(?:过)?\s*"
    r"(?:[：:,，]|[“\"「『]))"
)

_SELFIE = re.compile(r"(?:自拍|现在的照片)")
_FULL_BODY = re.compile(r"(?:全身照|穿|戴|换上|打扮成)")
_PORTRAIT = re.compile(r"(?:头像|近照|特写)")
_REQUIRED_PLANNER_FIELDS = (
    "triggered",
    "confidence",
    "intent",
    "subject",
    "capture_type",
    "mode",
    "normalized_request",
)
_TRIGGERED_INTENTS = frozenset({"generate", "edit"})

_SYSTEM_PROMPT = """你是图片意图分类器。判断用户当前消息是否在请求立即生成或编辑图片。
能力询问、否定请求、普通讨论和纯假设聊天不得触发。只输出一个 JSON 对象，不要输出 Markdown。
JSON 必须包含 triggered、confidence、intent、subject、capture_type、mode、normalized_request。
subject 只能是 auto、bot、user、other；capture_type 只能是 auto、selfie、portrait、full_body、scene；
mode 只能是 auto、text_to_image、identity_reference、image_to_image。"""


class IntentDetector:
    def __init__(
        self,
        llm: Any,
        *,
        planner_model: str = "planner",
        semantic_threshold: float = 0.80,
        bot_aliases: Sequence[str] = (),
        character_name: str = "",
    ) -> None:
        self._llm = llm
        self._planner_model = str(planner_model or "planner").strip()
        self._semantic_threshold = float(semantic_threshold)
        self._character_name = str(character_name or "").strip()
        self._bot_aliases = normalize_bot_aliases(
            bot_aliases,
            self._character_name,
        )

    async def detect(
        self,
        text: str,
        *,
        has_recent_image: bool = False,
    ) -> ImageIntentDecision:
        normalized = " ".join(str(text or "").split())
        if (
            not normalized
            or _REPORTED_OR_QUOTED_REQUEST.search(normalized)
            or _NEGATIVE.search(normalized)
            or _CAPABILITY.search(normalized)
            or _HOW_TO_OR_ADVICE.search(normalized)
            or _COST_OR_DURATION.search(normalized)
        ):
            return ImageIntentDecision.no_trigger()
        if (
            _PHOTO.search(normalized)
            or _DRAW.search(normalized)
            or _OUTFIT.search(normalized)
        ):
            return self._direct_decision(
                normalized,
                has_recent_image=has_recent_image,
            )
        if _EDIT.search(normalized):
            if not has_recent_image:
                return ImageIntentDecision.no_trigger()
            return self._direct_decision(normalized, has_recent_image=True)
        if not _AMBIGUOUS.search(normalized):
            return ImageIntentDecision.no_trigger()
        return await self._planner_decision(
            normalized,
            has_recent_image=has_recent_image,
        )

    def _direct_decision(
        self,
        normalized: str,
        *,
        has_recent_image: bool,
    ) -> ImageIntentDecision:
        is_edit = bool(_EDIT.search(normalized) and has_recent_image)
        subject = resolve_text_subject(normalized, self._bot_aliases)

        if _SELFIE.search(normalized):
            capture_type = CaptureType.SELFIE
        elif _FULL_BODY.search(normalized):
            capture_type = CaptureType.FULL_BODY
        elif _PORTRAIT.search(normalized):
            capture_type = CaptureType.PORTRAIT
        else:
            capture_type = CaptureType.AUTO

        if is_edit:
            mode = GenerationMode.IMAGE_TO_IMAGE
        elif subject is Subject.BOT:
            mode = GenerationMode.IDENTITY_REFERENCE
        else:
            mode = GenerationMode.TEXT_TO_IMAGE

        return ImageIntentDecision(
            triggered=True,
            confidence=1.0,
            intent="edit" if is_edit else "generate",
            subject=subject,
            capture_type=capture_type,
            mode=mode,
            normalized_request=normalized,
        )

    async def _planner_decision(
        self,
        normalized: str,
        *,
        has_recent_image: bool,
    ) -> ImageIntentDecision:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "text": normalized,
                        "has_recent_image": has_recent_image,
                        "character_name": self._character_name,
                        "bot_aliases": self._bot_aliases,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            result = await self._llm.generate(
                messages,
                model=self._planner_model,
                temperature=0,
                max_tokens=400,
            )
            if not isinstance(result, Mapping) or result.get("success") is not True:
                return ImageIntentDecision.no_trigger()
            payload = extract_json_object(result.get("response", ""))
            return self._decision_from_mapping(
                payload,
                has_recent_image=has_recent_image,
            )
        except Exception:
            return ImageIntentDecision.no_trigger()

    def _decision_from_mapping(
        self,
        payload: Mapping[str, Any],
        *,
        has_recent_image: bool,
    ) -> ImageIntentDecision:
        if any(field not in payload for field in _REQUIRED_PLANNER_FIELDS):
            return ImageIntentDecision.no_trigger()

        triggered = payload["triggered"]
        confidence = payload["confidence"]
        intent = payload["intent"]
        normalized_request = payload["normalized_request"]
        if not isinstance(triggered, bool):
            return ImageIntentDecision.no_trigger()
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return ImageIntentDecision.no_trigger()
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            return ImageIntentDecision.no_trigger()
        if not isinstance(intent, str):
            return ImageIntentDecision.no_trigger()
        intent = intent.strip().lower()
        if intent not in _TRIGGERED_INTENTS:
            return ImageIntentDecision.no_trigger()
        if not isinstance(normalized_request, str) or not normalized_request.strip():
            return ImageIntentDecision.no_trigger()

        try:
            subject = Subject(str(payload["subject"]).strip().lower())
            capture_type = CaptureType(
                str(payload["capture_type"]).strip().lower()
            )
            mode = GenerationMode(str(payload["mode"]).strip().lower())
        except (TypeError, ValueError):
            return ImageIntentDecision.no_trigger()

        if not triggered or confidence < self._semantic_threshold:
            return ImageIntentDecision.no_trigger()
        if intent == "edit" and mode is not GenerationMode.IMAGE_TO_IMAGE:
            return ImageIntentDecision.no_trigger()
        if mode is GenerationMode.IMAGE_TO_IMAGE:
            if intent != "edit" or not has_recent_image:
                return ImageIntentDecision.no_trigger()
        if (
            mode is GenerationMode.IDENTITY_REFERENCE
            and subject is not Subject.BOT
        ):
            return ImageIntentDecision.no_trigger()
        return ImageIntentDecision(
            triggered=True,
            confidence=confidence,
            intent=intent,
            subject=subject,
            capture_type=capture_type,
            mode=mode,
            normalized_request=normalized_request.strip(),
        )
