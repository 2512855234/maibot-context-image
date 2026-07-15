"""OpenAI-compatible Images API client."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin

import httpx

from ..errors import ConfigurationError, UpstreamError
from ..models import GeneratedImage, GenerationPlan, ImageReference
from ..parsing import detect_image_mime, parse_image_payload
from ..validation import validate_public_https_url


_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


def _extension(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(mime_type, ".bin")


class ImagesApiClient:
    def __init__(
        self,
        config: Any,
        http: httpx.AsyncClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        url_validator: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._sleep = sleep
        if url_validator is None:
            block_private_networks = bool(
                getattr(config, "block_private_networks", True)
            )
            allowed_hosts = tuple(getattr(config, "allowed_image_hosts", ()))
            self._url_validator = lambda url: validate_public_https_url(
                url,
                block_private_networks=block_private_networks,
                allowed_hosts=allowed_hosts,
            )
        else:
            self._url_validator = url_validator

    async def generate(self, plan: GenerationPlan) -> GeneratedImage:
        if not str(getattr(self._config, "api_key", "")).strip():
            raise ConfigurationError("图片 API Key 未配置。")
        response = await self._post_with_retries(plan)
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError("图片服务返回了无法解析的响应。") from exc
        kind, value = parse_image_payload(payload)
        if kind == "base64":
            try:
                data = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise UpstreamError("图片服务返回的图片数据无效。") from exc
        else:
            data = await self._download(value)

        max_bytes = int(getattr(self._config, "max_output_bytes", 50 * 1024 * 1024))
        if not data or len(data) > max_bytes:
            raise UpstreamError("图片服务返回的文件为空或超过大小限制。")
        mime_type = detect_image_mime(data)
        revised_prompt = ""
        items = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict):
            revised_prompt = str(items[0].get("revised_prompt") or "")
        return GeneratedImage(
            data=data,
            mime_type=mime_type,
            route="images",
            revised_prompt=revised_prompt,
        )

    async def _post_with_retries(self, plan: GenerationPlan) -> httpx.Response:
        max_retries = max(0, int(getattr(self._config, "max_retries", 2)))
        for attempt in range(max_retries + 1):
            try:
                response = await self._post(plan)
            except _RETRYABLE_EXCEPTIONS as exc:
                if attempt >= max_retries:
                    raise UpstreamError(
                        "图片服务暂时无法连接，请稍后重试。",
                        retryable=True,
                    ) from exc
                await self._sleep(float(min(2**attempt, 4)))
                continue

            if response.status_code < 400:
                return response
            retryable = response.status_code in _RETRYABLE_STATUS
            if retryable and attempt < max_retries:
                await self._sleep(self._retry_delay(response, attempt))
                continue
            raise self._status_error(response.status_code, retryable)
        raise UpstreamError("图片服务请求失败。")

    async def _post(self, plan: GenerationPlan) -> httpx.Response:
        base_url = str(self._config.base_url).rstrip("/")
        path = (
            self._config.generations_path
            if plan.operation == "generation"
            else self._config.edits_path
        )
        url = base_url + "/" + str(path).lstrip("/")
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Accept": "application/json",
        }
        common = {
            "model": str(self._config.model),
            "prompt": plan.final_prompt,
            "size": str(getattr(self._config, "size", "1024x1536")),
            "quality": str(getattr(self._config, "quality", "high")),
            "output_format": str(getattr(self._config, "output_format", "png")),
        }
        timeout = float(getattr(self._config, "timeout_seconds", 300.0))
        if plan.operation == "generation":
            payload = {
                **common,
                "background": str(getattr(self._config, "background", "auto")),
                "moderation": str(getattr(self._config, "moderation", "auto")),
                "n": 1,
            }
            return await self._http.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )

        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for reference in plan.references:
            filename = reference.role.value + _extension(reference.mime_type)
            files.append(
                (
                    "image[]",
                    (filename, reference.data, reference.mime_type),
                )
            )
        return await self._http.post(
            url,
            headers=headers,
            data=common,
            files=files,
            timeout=timeout,
        )

    async def _download(self, url: str) -> bytes:
        current = url
        max_bytes = int(getattr(self._config, "max_output_bytes", 50 * 1024 * 1024))
        for _ in range(6):
            self._url_validator(current)
            response = await self._http.get(current, follow_redirects=False)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise UpstreamError("图片下载重定向缺少目标地址。")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise UpstreamError("图片下载失败。", status_code=response.status_code)
            data = response.content
            if len(data) > max_bytes:
                raise UpstreamError("下载图片超过大小限制。")
            return data
        raise UpstreamError("图片下载重定向次数过多。")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After", "")
        try:
            return max(0.0, min(float(value), 60.0))
        except ValueError:
            return float(min(2**attempt, 4))

    @staticmethod
    def _status_error(status_code: int, retryable: bool) -> UpstreamError:
        if status_code in {401, 403}:
            message = "图片服务认证失败，请检查 API Key 和访问权限。"
        elif status_code == 429:
            message = "图片服务请求过于频繁，请稍后重试。"
        elif status_code == 400:
            message = "图片服务拒绝了请求参数或内容。"
        else:
            message = "图片服务暂时不可用，请稍后重试。"
        return UpstreamError(
            message,
            status_code=status_code,
            retryable=retryable,
        )
