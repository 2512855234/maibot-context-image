"""Recent image cache and deterministic generation planning."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import replace
from time import time

from .errors import IdentityRequiredError, ImageSourceRequiredError
from .models import (
    CompiledPrompt,
    GenerationMode,
    GenerationPlan,
    GenerationRequest,
    ImageReference,
    Subject,
)
from .prompt_assembler import assemble_prompt
from .trigger import resolve_subject


class RecentImageCache:
    def __init__(
        self,
        max_per_stream: int,
        max_age_seconds: float,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        self._max_per_stream = max(1, int(max_per_stream))
        self._max_age_seconds = max(0.0, float(max_age_seconds))
        self._clock = clock
        self._items: dict[str, deque[ImageReference]] = defaultdict(
            lambda: deque(maxlen=self._max_per_stream)
        )

    def put(self, stream_id: str, image: ImageReference) -> None:
        value = image
        if value.timestamp <= 0:
            value = replace(value, timestamp=self._clock())
        self._items[str(stream_id)].append(value)

    def clear_expired(self) -> None:
        now = self._clock()
        empty: list[str] = []
        for stream_id, images in self._items.items():
            kept = [
                image
                for image in images
                if now - image.timestamp <= self._max_age_seconds
            ]
            images.clear()
            images.extend(kept)
            if not images:
                empty.append(stream_id)
        for stream_id in empty:
            self._items.pop(stream_id, None)

    def latest(self, stream_id: str) -> ImageReference | None:
        self.clear_expired()
        images = self._items.get(str(stream_id))
        return images[-1] if images else None

    def has_recent(self, stream_id: str) -> bool:
        images = self._items.get(str(stream_id))
        if not images:
            return False
        now = self._clock()
        return any(
            now - image.timestamp <= self._max_age_seconds
            for image in images
        )

    def items(self, stream_id: str) -> tuple[ImageReference, ...]:
        self.clear_expired()
        return tuple(self._items.get(str(stream_id), ()))


def build_generation_plan(
    request: GenerationRequest,
    compiled: CompiledPrompt,
    base_image: ImageReference | None,
    identity_image: ImageReference | None,
    aliases: Sequence[str],
    character_name: str = "Bot",
) -> GenerationPlan:
    subject = resolve_subject(request, aliases, character_name)
    if request.subject is Subject.AUTO and request.use_bot_identity is True:
        subject = Subject.BOT

    if request.mode is GenerationMode.IMAGE_TO_IMAGE and base_image is None:
        raise ImageSourceRequiredError()

    if subject is Subject.BOT and identity_image is None:
        raise IdentityRequiredError()

    reference_note = ""
    if base_image is not None:
        if subject is Subject.BOT:
            references = (identity_image, base_image)
            reference_note = (
                "参考图职责：第一张固定身份图决定人物面容；"
                "第二张待编辑图只参考构图和场景。"
            )
        else:
            references = (base_image,)
        operation = "edit"
        mode = GenerationMode.IMAGE_TO_IMAGE
    elif identity_image is not None:
        references = (identity_image,)
        operation = "edit"
        mode = GenerationMode.IDENTITY_REFERENCE
    else:
        references = ()
        operation = "generation"
        mode = GenerationMode.TEXT_TO_IMAGE

    final_prompt = assemble_prompt(
        compiled,
        request,
        subject,
        character_name=character_name,
    )
    if reference_note:
        final_prompt = f"{final_prompt}\n{reference_note}"

    return GenerationPlan(
        request=request,
        mode=mode,
        subject=subject,
        final_prompt=final_prompt,
        references=references,
        operation=operation,
    )
