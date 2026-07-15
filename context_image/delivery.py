"""Send generated images exactly once and shape Tool results."""

from __future__ import annotations

import base64
from typing import Any

from .errors import DeliveryError
from .models import GeneratedImage, GenerationMode


class Delivery:
    def __init__(
        self,
        sender: Any,
        *,
        include_tool_media: bool = True,
        show_final_prompt: bool = False,
    ) -> None:
        self._sender = sender
        self._include_tool_media = include_tool_media
        self._show_final_prompt = show_final_prompt

    async def send(
        self,
        image: GeneratedImage,
        stream_id: str,
        final_prompt: str,
        *,
        mode: GenerationMode,
        used_context: bool,
        used_bot_identity: bool,
    ) -> dict[str, Any]:
        encoded = base64.b64encode(image.data).decode("ascii")
        sent = await self._sender.image(encoded, stream_id)
        if not sent:
            raise DeliveryError()

        result: dict[str, Any] = {
            "success": True,
            "content": "已根据当前聊天生成并发送图片，请勿重复发送。",
            "already_sent": True,
            "mode": mode.value,
            "used_context": used_context,
            "used_bot_identity": used_bot_identity,
            "route": {"api_mode": image.route},
        }
        if self._show_final_prompt:
            result["final_prompt"] = final_prompt
        if self._include_tool_media:
            result["content_items"] = [
                {
                    "type": "image",
                    "data": encoded,
                    "mime_type": image.mime_type,
                    "name": "generated-image" + _extension(image.mime_type),
                    "description": "根据当前聊天上下文生成的 AI 图片",
                }
            ]
        return result


def _extension(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(mime_type, ".bin")

