"""Framework-independent end-to-end generation orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .errors import ConfigurationError, ImageSourceRequiredError
from .image_sources import build_generation_plan
from .models import (
    GenerationMode,
    GenerationOutcome,
    GenerationRequest,
    ImageReference,
    Subject,
)
from .trigger import resolve_subject


_EDIT_CUES = ("这张", "这幅", "改成", "换成", "变成", "编辑", "修改")


class GenerationService:
    def __init__(
        self,
        *,
        context_collector: Any,
        prompt_compiler: Any,
        identity_store: Any,
        image_cache: Any,
        images_api: Any,
        bot_aliases: Sequence[str],
        identity_enabled: bool,
        character_name: str = "Bot",
        reference_policy: str = "fixed_only",
    ) -> None:
        if reference_policy != "fixed_only":
            raise ConfigurationError(
                "Bot 身份参考策略仅支持 fixed_only。"
            )
        self._context_collector = context_collector
        self._prompt_compiler = prompt_compiler
        self._identity_store = identity_store
        self._image_cache = image_cache
        self._images_api = images_api
        self._bot_aliases = tuple(bot_aliases)
        self._identity_enabled = identity_enabled
        self._character_name = character_name
        self._reference_policy = reference_policy
        self._previous_prompts: dict[str, str] = {}

    def previous_prompt(self, stream_id: str) -> str:
        return self._previous_prompts.get(stream_id, "")

    def base_image_id(self, stream_id: str) -> str:
        image = self._image_cache.latest(stream_id)
        return image.image_id if image is not None else ""

    def has_recent_image(self, stream_id: str) -> bool:
        return bool(self._image_cache.has_recent(stream_id))

    async def generate(
        self,
        stream_id: str,
        chat_id: str,
        request: GenerationRequest,
    ) -> GenerationOutcome:
        subject = resolve_subject(
            request,
            self._bot_aliases,
            self._character_name,
        )
        if (
            request.subject is Subject.AUTO
            and request.use_bot_identity is True
        ):
            subject = Subject.BOT

        recent_image = self._image_cache.latest(stream_id)
        if (
            request.mode is GenerationMode.IMAGE_TO_IMAGE
            and recent_image is None
        ):
            raise ImageSourceRequiredError()

        identity_image: ImageReference | None = None
        if subject is Subject.BOT:
            identity_image = self._identity_store.load()

        base_image = (
            recent_image
            if self._uses_recent_image(request, recent_image is not None)
            else None
        )

        snapshot = await self._context_collector.collect(
            chat_id,
            request.request,
            previous_prompt=self.previous_prompt(stream_id),
        )
        compiled = await self._prompt_compiler.compile(
            snapshot,
            request,
            subject,
        )
        plan_request = request
        if request.subject is Subject.AUTO and subject is not Subject.AUTO:
            plan_request = GenerationRequest(
                request=request.request,
                mode=request.mode,
                subject=subject,
                capture_type=request.capture_type,
                use_context=request.use_context,
                prompt_join_mode=request.prompt_join_mode,
                prompt_fragments=request.prompt_fragments,
                use_bot_identity=request.use_bot_identity,
            )
        plan = build_generation_plan(
            plan_request,
            compiled,
            base_image,
            identity_image,
            self._bot_aliases,
            self._character_name,
        )
        image = await self._images_api.generate(plan)
        self._previous_prompts[stream_id] = plan.final_prompt
        return GenerationOutcome(
            image=image,
            plan=plan,
            used_context=request.use_context,
            used_bot_identity=identity_image is not None,
        )

    @staticmethod
    def _uses_recent_image(
        request: GenerationRequest,
        has_recent_image: bool,
    ) -> bool:
        if not has_recent_image:
            return False
        if request.mode is GenerationMode.TEXT_TO_IMAGE:
            return False
        if request.mode is GenerationMode.IMAGE_TO_IMAGE:
            return True
        return any(cue in request.request for cue in _EDIT_CUES)
