"""Framework-independent domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class GenerationMode(str, Enum):
    AUTO = "auto"
    TEXT_TO_IMAGE = "text_to_image"
    IDENTITY_REFERENCE = "identity_reference"
    IMAGE_TO_IMAGE = "image_to_image"


class Subject(str, Enum):
    AUTO = "auto"
    BOT = "bot"
    USER = "user"
    OTHER = "other"


class CaptureType(str, Enum):
    AUTO = "auto"
    SELFIE = "selfie"
    PORTRAIT = "portrait"
    FULL_BODY = "full_body"
    SCENE = "scene"


class ReferenceRole(str, Enum):
    BASE = "base"
    IDENTITY = "identity"


_JOIN_MODES = frozenset({"smart_merge", "append", "override"})


@dataclass(frozen=True, slots=True)
class ImageIntentDecision:
    triggered: bool
    confidence: float = 0.0
    intent: str = "none"
    subject: Subject = Subject.OTHER
    capture_type: CaptureType = CaptureType.AUTO
    mode: GenerationMode = GenerationMode.AUTO
    normalized_request: str = ""

    @classmethod
    def no_trigger(cls) -> "ImageIntentDecision":
        return cls(False)


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    request: str
    mode: GenerationMode = GenerationMode.AUTO
    subject: Subject = Subject.AUTO
    capture_type: CaptureType = CaptureType.AUTO
    use_context: bool = True
    prompt_join_mode: str = "smart_merge"
    prompt_fragments: tuple[str, ...] = ()
    use_bot_identity: bool | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GenerationRequest":
        request = str(value.get("request", "")).strip()
        if not request:
            raise ValueError("图片请求不能为空")

        raw_fragments = value.get("prompt_fragments", ())
        if isinstance(raw_fragments, str):
            raw_fragments = (raw_fragments,)
        fragments = tuple(
            fragment
            for item in raw_fragments or ()
            if (fragment := str(item).strip())
        )

        join_mode = str(value.get("prompt_join_mode", "smart_merge")).strip().lower()
        if join_mode not in _JOIN_MODES:
            raise ValueError(f"不支持的提示词拼接模式：{join_mode}")

        identity_value = value.get("use_bot_identity")
        if identity_value is not None and not isinstance(identity_value, bool):
            raise ValueError("use_bot_identity 必须是布尔值或 null")

        return cls(
            request=request,
            mode=GenerationMode(str(value.get("mode", "auto")).strip().lower()),
            subject=Subject(str(value.get("subject", "auto")).strip().lower()),
            capture_type=CaptureType(
                str(value.get("capture_type", "auto")).strip().lower()
            ),
            use_context=bool(value.get("use_context", True)),
            prompt_join_mode=join_mode,
            prompt_fragments=fragments,
            use_bot_identity=identity_value,
        )


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    current_request: str
    recent_text: str = ""
    reply_text: str = ""
    previous_prompt: str = ""
    image_descriptions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    intent: str = "generate"
    subject: Subject = Subject.OTHER
    capture_type: CaptureType = CaptureType.AUTO
    scene: str = ""
    activity: str = ""
    expression: str = ""
    outfit: str = ""
    composition: str = ""
    lighting: str = ""
    style: str = ""
    must_preserve: tuple[str, ...] = ()
    must_change: tuple[str, ...] = ()
    negative: tuple[str, ...] = field(
        default_factory=lambda: ("文字", "水印", "面部畸变")
    )


@dataclass(frozen=True, slots=True)
class ImageReference:
    image_id: str
    data: bytes
    mime_type: str
    source: str
    role: ReferenceRole
    description: str = ""
    timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    request: GenerationRequest
    mode: GenerationMode
    subject: Subject
    final_prompt: str
    references: tuple[ImageReference, ...]
    operation: str


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    route: str
    revised_prompt: str = ""


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    image: GeneratedImage
    plan: GenerationPlan
    used_context: bool
    used_bot_identity: bool
