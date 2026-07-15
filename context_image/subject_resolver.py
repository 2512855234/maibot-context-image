"""Shared context-aware Bot subject resolution."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import Subject


_CONTEXTUAL_PRONOUN_ALIASES = frozenset({"你", "您", "妳"})
_PRONOUN_VISUAL_SUBJECT = re.compile(
    r"(?:你|您|妳)(?:本人|现在|正在)?(?:的)?(?:"
    r"照片|自拍|头像|全身照|近照|特写|样子|"
    r"穿|戴|换上|打扮成"
    r")"
)
_PRONOUN_LOCATION_VISUAL_SUBJECT = re.compile(
    r"(?:生成|画|制作|拍|发|来|想看|看看|让我看)"
    r"(?:(?!(?:生成|画|制作)).){0,16}"
    r"(?:你|您|妳)(?:本人)?在"
    r"(?:(?!(?:生成|画|制作)).){0,24}"
    r"(?:照片|自拍|头像|全身照|近照|特写|样子)"
)
_CJK_LEFT_CONTEXT = (
    r"(?:^|[\s，。！？!?：:；;、（(《〈「『“\"']|"
    r"(?:想看|看看|看|生成|画|拍|发|做|给|让|把|喜欢|张|幅|个))"
)
_CJK_SUBJECT_FOLLOW = (
    r"(?:现在|正在|穿|戴|换上|打扮成|在|"
    r"的(?:照片|自拍|头像|全身照|近照|特写|样子)|"
    r"拿|站|坐|躺|跑|走|笑|哭)"
)


def normalize_bot_aliases(
    aliases: Sequence[str],
    character_name: str = "",
) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_alias in (*tuple(aliases or ()), character_name):
        alias = str(raw_alias or "").strip().casefold()
        if alias and alias not in normalized:
            normalized.append(alias)
    return tuple(normalized)


def resolve_text_subject(
    text: str,
    aliases: Sequence[str],
    character_name: str = "",
) -> Subject:
    folded = str(text or "").casefold()
    normalized_aliases = normalize_bot_aliases(aliases, character_name)

    if (
        _PRONOUN_VISUAL_SUBJECT.search(folded)
        or _PRONOUN_LOCATION_VISUAL_SUBJECT.search(folded)
    ):
        return Subject.BOT

    for alias in normalized_aliases:
        if alias in _CONTEXTUAL_PRONOUN_ALIASES:
            continue
        if _alias_names_visual_subject(folded, alias):
            return Subject.BOT
    return Subject.OTHER


def _alias_names_visual_subject(text: str, alias: str) -> bool:
    if any(character.isascii() and character.isalnum() for character in alias):
        pattern = re.compile(
            r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
        )
        return bool(pattern.search(text))

    pattern = re.compile(
        _CJK_LEFT_CONTEXT
        + re.escape(alias)
        + rf"(?=$|[\s，。！？!?：:；;、）)》〉」』”\"']|{_CJK_SUBJECT_FOLLOW})"
    )
    return bool(pattern.search(text))
