"""Priority-aware natural-language prompt assembly."""

from __future__ import annotations

from .models import CompiledPrompt, GenerationRequest, Subject


_IDENTITY_CONTRACT = (
    "将固定身份参考图作为 Bot 人物的唯一规范外貌。保持相同的面部身份、面部几何、"
    "眼型、鼻子、嘴唇、下颌线、发际线、发型、发色、眼睛颜色和表观年龄。"
    "只改变当前场景要求的表情、姿势、服装、构图、光线和背景；不要重新设计人物面孔。"
)


def _append_unique(parts: list[str], seen: set[str], text: str) -> None:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return
    key = normalized.casefold()
    if key in seen:
        return
    seen.add(key)
    parts.append(normalized)


def assemble_prompt(
    compiled: CompiledPrompt,
    request: GenerationRequest,
    subject: Subject,
    *,
    character_name: str = "Bot",
) -> str:
    parts: list[str] = []
    seen: set[str] = set()

    if subject is Subject.BOT:
        _append_unique(
            parts,
            seen,
            f"固定身份角色：{character_name}。身份约束：{_IDENTITY_CONTRACT}",
        )

    _append_unique(parts, seen, f"当前要求：{request.request}")

    if request.prompt_join_mode != "override":
        fields = (
            ("场景", compiled.scene),
            ("动作", compiled.activity),
            ("表情", compiled.expression),
            ("服装", compiled.outfit),
            ("构图", compiled.composition),
            ("光线", compiled.lighting),
            ("风格", compiled.style),
        )
        for label, value in fields:
            if value:
                _append_unique(parts, seen, f"{label}：{value}")

    for fragment in request.prompt_fragments:
        _append_unique(parts, seen, f"补充要求：{fragment}")

    for item in compiled.must_preserve:
        _append_unique(parts, seen, f"必须保留：{item}")
    for item in compiled.must_change:
        _append_unique(parts, seen, f"必须修改：{item}")
    if compiled.negative:
        _append_unique(parts, seen, "避免：" + "、".join(compiled.negative))

    return "\n".join(parts)
