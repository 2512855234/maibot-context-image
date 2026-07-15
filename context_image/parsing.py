"""Normalize image API responses and actual byte formats."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import UpstreamError


def detect_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise UpstreamError("图片服务返回了不受支持的图片格式。")


def parse_image_payload(payload: Mapping[str, Any]) -> tuple[str, str]:
    items = payload.get("data")
    if not isinstance(items, list):
        raise UpstreamError("图片服务没有返回图片数据。")
    for item in items:
        if not isinstance(item, Mapping):
            continue
        b64_json = item.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            return "base64", b64_json.strip()
        url = item.get("url")
        if isinstance(url, str) and url.strip():
            return "url", url.strip()
    raise UpstreamError("图片服务没有返回图片数据。")

