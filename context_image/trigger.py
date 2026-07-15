"""Deterministic safeguards around LLM tool selection."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import GenerationMode, GenerationRequest, Subject
from .subject_resolver import resolve_text_subject


_NEGATIVE_PATTERNS = (
    re.compile(r"(?:不要|别|不需要|无需).{0,8}(?:生成|画|拍|做).{0,4}(?:图|照片|自拍|头像)"),
    re.compile(r"(?:你)?(?:会不会|会|能不能|能).{0,4}(?:画图|画画|拍照|生图)(?:吗|么|\?|？)?$"),
    re.compile(r"^如果.{0,16}(?:画成|生成).{0,8}(?:会|将会|可能会)"),
)

_POSITIVE_PATTERNS = (
    re.compile(r"(?:拍|发)(?:一|1)?张.{0,10}(?:照片|自拍)"),
    re.compile(r"(?:画|生成|做)(?:一|1)?(?:张|个|幅)?"),
    re.compile(r"(?:改成|换成|变成|编辑这张|修改这张)"),
    re.compile(r"(?:头像|立绘|自画像|场景图|配图)"),
)


def is_explicit_image_request(text: str) -> bool:
    normalized = " ".join(str(text).strip().split())
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in _NEGATIVE_PATTERNS):
        return False
    return any(pattern.search(normalized) for pattern in _POSITIVE_PATTERNS)


def resolve_subject(
    request: GenerationRequest,
    aliases: Sequence[str],
    character_name: str = "",
) -> Subject:
    if request.subject is not Subject.AUTO:
        return request.subject
    return resolve_text_subject(request.request, aliases, character_name)


def resolve_mode(
    request: GenerationRequest,
    subject: Subject,
    *,
    has_base_image: bool,
    identity_enabled: bool,
) -> GenerationMode:
    if has_base_image:
        return GenerationMode.IMAGE_TO_IMAGE
    if subject is Subject.BOT and identity_enabled:
        return GenerationMode.IDENTITY_REFERENCE
    if request.mode is not GenerationMode.AUTO:
        return request.mode
    return GenerationMode.TEXT_TO_IMAGE
