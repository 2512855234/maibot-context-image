"""MaiBot SDK adapter for Context Image."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import httpx
from maibot_sdk import (
    CONFIG_RELOAD_SCOPE_SELF,
    Command,
    EventHandler,
    HookHandler,
    MaiBotPlugin,
    Tool,
)
from maibot_sdk.types import EventType, HookMode, ToolParameterInfo, ToolParamType

from .context_image.clients.images_api import ImagesApiClient
from .context_image.config import ContextImageConfig
from .context_image.context_collector import ContextCollector
from .context_image.delivery import Delivery
from .context_image.errors import ContextImageError
from .context_image.generation_service import GenerationService
from .context_image.identity import IdentityStore
from .context_image.image_sources import RecentImageCache
from .context_image.intent_detector import IntentDetector
from .context_image.message_parser import is_private_message, parse_trigger_message
from .context_image.models import GenerationRequest
from .context_image.prompt_compiler import PromptCompiler
from .context_image.reply_gate import ReplyGate
from .context_image.runtime import PluginRuntime
from .context_image.trigger_coordinator import TriggerCoordinator, TriggerJob


class ContextImagePlugin(MaiBotPlugin):
    config_model = ContextImageConfig

    def __init__(self) -> None:
        super().__init__()
        self._runtime: PluginRuntime | None = None
        self._http: httpx.AsyncClient | None = None
        self._intent_detector: IntentDetector | None = None
        self._reply_gate: ReplyGate | None = None
        self._coordinator: TriggerCoordinator | None = None

    def get_components(self) -> list[dict[str, Any]]:
        components = super().get_components()
        for component in components:
            metadata = component.get("metadata")
            if not isinstance(metadata, dict):
                continue
            declared = metadata.get("metadata")
            if isinstance(declared, dict):
                for key in ("timeout_ms", "chat_scope"):
                    if key in declared:
                        metadata.setdefault(key, declared[key])
        return components

    async def on_load(self) -> None:
        self.ctx.paths.data_dir.mkdir(parents=True, exist_ok=True)
        self.ctx.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        await self._rebuild_runtime()
        self.ctx.logger.info("MaiBot Context Image 已加载")

    async def on_unload(self) -> None:
        await self._close_runtime()
        self.ctx.logger.info("MaiBot Context Image 已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            await self._rebuild_runtime()
            self.ctx.logger.info("图片插件配置已更新: version=%s", version)

    @Tool(
        "generate_image",
        timeout_ms=360_000,
        chat_scope="private",
        brief_description=(
            "当用户明确要求得到图片成品、自拍、头像、场景图或编辑图片时调用。"
            "不要因能力询问、假设讨论或否定请求调用。"
        ),
        detailed_description=(
            "根据当前聊天上下文实时优化提示词并生成图片。画 Bot 本人时 subject=bot，"
            "插件会强制使用固定身份图；编辑最近图片时 mode=image_to_image。"
        ),
        parameters=[
            ToolParameterInfo(
                name="request",
                param_type=ToolParamType.STRING,
                description="当前图片生成或编辑要求",
                required=True,
            ),
            ToolParameterInfo(
                name="stream_id",
                param_type=ToolParamType.STRING,
                description="当前聊天流 ID",
                required=True,
            ),
            ToolParameterInfo(
                name="mode",
                param_type=ToolParamType.STRING,
                enum_values=["auto", "text_to_image", "identity_reference", "image_to_image"],
                required=False,
                default="auto",
            ),
            ToolParameterInfo(
                name="subject",
                param_type=ToolParamType.STRING,
                enum_values=["auto", "bot", "user", "other"],
                required=False,
                default="auto",
            ),
            ToolParameterInfo(
                name="capture_type",
                param_type=ToolParamType.STRING,
                enum_values=["auto", "selfie", "portrait", "full_body", "scene"],
                required=False,
                default="auto",
            ),
            ToolParameterInfo(
                name="use_context",
                param_type=ToolParamType.BOOLEAN,
                required=False,
                default=True,
            ),
            ToolParameterInfo(
                name="prompt_join_mode",
                param_type=ToolParamType.STRING,
                enum_values=["smart_merge", "append", "override"],
                required=False,
                default="smart_merge",
            ),
            ToolParameterInfo(
                name="prompt_fragments",
                param_type=ToolParamType.ARRAY,
                items_schema={"type": "string"},
                required=False,
            ),
            ToolParameterInfo(
                name="use_bot_identity",
                param_type=ToolParamType.BOOLEAN,
                required=False,
            ),
        ],
    )
    async def handle_generate_image(
        self,
        request: str,
        stream_id: str,
        mode: str = "auto",
        subject: str = "auto",
        capture_type: str = "auto",
        use_context: bool = True,
        prompt_join_mode: str = "smart_merge",
        prompt_fragments: list[str] | None = None,
        use_bot_identity: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if kwargs.get("group_id"):
            return self._private_only_result()
        return await self._run_request(
            stream_id,
            request,
            chat_id=str(kwargs.get("chat_id") or stream_id),
            source_message_id=self._source_message_id(kwargs),
            mode=mode,
            subject=subject,
            capture_type=capture_type,
            use_context=use_context,
            prompt_join_mode=prompt_join_mode,
            prompt_fragments=prompt_fragments or [],
            use_bot_identity=use_bot_identity,
        )

    @Command(
        "draw_image",
        description="根据描述生成图片",
        pattern=r"^/(?:生图|画图)\s+(?P<request>.+)$",
        timeout_ms=360_000,
        chat_scope="private",
    )
    async def handle_draw_command(self, **kwargs: Any):
        if kwargs.get("group_id"):
            return False, self._private_only_result()["content"], 2
        request = kwargs.get("matched_groups", {}).get("request", "").strip()
        result = await self._run_request(
            kwargs["stream_id"],
            request,
            chat_id=str(kwargs.get("chat_id") or kwargs["stream_id"]),
            source_message_id=self._source_message_id(kwargs),
            mode="auto",
        )
        return bool(result.get("success")), str(result.get("content", "")), 2

    @Command(
        "edit_image",
        description="编辑最近收到的图片",
        pattern=r"^/(?:图生图|改图)\s+(?P<request>.+)$",
        timeout_ms=360_000,
        chat_scope="private",
    )
    async def handle_edit_command(self, **kwargs: Any):
        if kwargs.get("group_id"):
            return False, self._private_only_result()["content"], 2
        request = kwargs.get("matched_groups", {}).get("request", "").strip()
        result = await self._run_request(
            kwargs["stream_id"],
            request,
            chat_id=str(kwargs.get("chat_id") or kwargs["stream_id"]),
            source_message_id=self._source_message_id(kwargs),
            mode="image_to_image",
        )
        return bool(result.get("success")), str(result.get("content", "")), 2

    @Command(
        "image_status",
        description="检查图片插件和身份图状态",
        pattern=r"^/image\s+status$",
        chat_scope="private",
    )
    async def handle_status_command(self, **kwargs: Any):
        if kwargs.get("group_id"):
            return False, self._private_only_result()["content"], 2
        if self._runtime is None:
            return False, "图片插件尚未初始化。", 2
        status = self._runtime.status()
        api_text = "已配置" if status.get("api_key_configured") else "未配置"
        identity_text = "已配置" if status.get("identity_configured") else "未配置"
        return True, f"图片 API：{api_text}；身份图：{identity_text}", 2

    @EventHandler(
        "cache_message_images",
        description="缓存最近聊天图片供图生图使用",
        event_type=EventType.ON_MESSAGE,
        intercept_message=False,
        chat_scope="private",
    )
    async def handle_message(self, message: Any, **kwargs: Any) -> None:
        await self._handle_incoming_message(message, **kwargs)

    @HookHandler(
        "chat.receive.after_process",
        name="context_image_after_process",
        description="在私聊消息预处理后检测自然语言生图请求",
        mode=HookMode.OBSERVE,
        chat_scope="private",
    )
    async def handle_after_process_hook(
        self,
        message: Any,
        **kwargs: Any,
    ) -> None:
        await self._handle_incoming_message(message, **kwargs)

    async def _handle_incoming_message(
        self,
        message: Any,
        **kwargs: Any,
    ) -> None:
        if not is_private_message(message):
            return
        runtime = self._runtime
        if runtime is None:
            return
        message_stream_id = (
            message.get("session_id") if isinstance(message, dict) else ""
        )
        stream_id = str(kwargs.get("stream_id") or message_stream_id or "").strip()
        if not stream_id:
            return
        try:
            runtime.cache_message_image(stream_id, message)
            parsed = parse_trigger_message(message, stream_id)
            if parsed is None or not self.config.auto_trigger.enabled:
                return
            detector = self._intent_detector
            reply_gate = self._reply_gate
            coordinator = self._coordinator
            if detector is None or reply_gate is None or coordinator is None:
                return
            decision = await detector.detect(
                parsed.text,
                has_recent_image=runtime.has_recent_image(stream_id),
            )
            if not decision.triggered:
                return
            request = GenerationRequest(
                request=decision.normalized_request or parsed.text,
                mode=decision.mode,
                subject=decision.subject,
                capture_type=decision.capture_type,
            )
            await coordinator.submit(
                TriggerJob(
                    source_message_id=parsed.message_id,
                    stream_id=parsed.stream_id,
                    chat_id=parsed.stream_id,
                    request=request,
                    before_delivery=lambda gate=reply_gate: gate.wait(
                        parsed.stream_id,
                        parsed.message_id,
                        parsed.user_id,
                        parsed.timestamp,
                    ),
                )
            )
        except ContextImageError as exc:
            self.ctx.logger.warning("自动图片任务未提交: %s", exc.public_message)
        except Exception:
            self.ctx.logger.exception("自动图片任务处理发生未预期错误")

    async def _run_request(
        self,
        stream_id: str,
        request: str,
        *,
        chat_id: str | None = None,
        source_message_id: str = "",
        **values: Any,
    ) -> dict[str, Any]:
        if self._runtime is None or self._coordinator is None:
            return {"success": False, "content": "图片插件尚未初始化。"}
        try:
            generation_request = GenerationRequest.from_mapping(
                {"request": request, **values}
            )
            return await self._coordinator.submit(
                TriggerJob(
                    source_message_id=source_message_id,
                    stream_id=stream_id,
                    chat_id=chat_id or stream_id,
                    request=generation_request,
                )
            )
        except (ContextImageError, ValueError) as exc:
            message = getattr(exc, "public_message", str(exc))
            return {"success": False, "content": message}
        except Exception:
            if hasattr(self, "ctx"):
                self.ctx.logger.exception("图片生成发生未预期错误")
            return {"success": False, "content": "图片生成失败，请稍后重试。"}

    @staticmethod
    def _private_only_result() -> dict[str, Any]:
        return {"success": False, "content": "图片功能仅支持私聊。"}

    @staticmethod
    def _source_message_id(values: dict[str, Any]) -> str:
        for key in ("source_message_id", "message_id"):
            message_id = str(values.get(key) or "").strip()
            if message_id:
                return message_id
        message = values.get("message")
        if isinstance(message, dict):
            return str(message.get("message_id") or "").strip()
        return ""

    async def _rebuild_runtime(self) -> None:
        await self._close_runtime()
        config = self.config
        api_key = config.api.api_key.strip() or os.getenv(config.api.api_key_env, "").strip()
        try:
            self._http = httpx.AsyncClient(verify=config.network.verify_ssl)
            settings = SimpleNamespace(
                base_url=config.api.base_url,
                api_key=api_key,
                model=config.api.model,
                generations_path=config.api.generations_path,
                edits_path=config.api.edits_path,
                timeout_seconds=config.api.timeout_seconds,
                size=config.generation.size,
                quality=config.generation.quality,
                output_format=config.generation.output_format,
                background=config.generation.background,
                moderation=config.generation.moderation,
                max_retries=config.network.max_retries,
                max_output_bytes=config.network.max_output_bytes,
                block_private_networks=config.network.block_private_networks,
                allowed_image_hosts=config.network.allowed_image_hosts,
            )
            image_cache = RecentImageCache(
                config.behavior.max_cached_images,
                config.behavior.reference_image_max_age_seconds,
            )
            identity_store = IdentityStore(
                self.ctx.paths.data_dir,
                config.identity.reference_filename,
                config.network.max_input_bytes,
            )
            service = GenerationService(
                context_collector=ContextCollector(
                    self.ctx.message,
                    message_limit=config.context.message_limit,
                    max_chars=config.context.max_chars,
                    include_previous_prompt=config.context.include_previous_prompt,
                ),
                prompt_compiler=PromptCompiler(
                    self.ctx.llm,
                    model=config.prompt.model,
                    temperature=config.prompt.temperature,
                    max_tokens=config.prompt.max_tokens,
                    fallback_to_template=config.prompt.fallback_to_template,
                    everyday_photo_defaults=config.prompt.everyday_photo_defaults,
                    default_style=config.prompt.default_style,
                    default_composition=config.prompt.default_composition,
                    default_lighting=config.prompt.default_lighting,
                    default_negative=config.prompt.default_negative,
                ),
                identity_store=identity_store,
                image_cache=image_cache,
                images_api=ImagesApiClient(settings, self._http),
                bot_aliases=config.identity.bot_aliases,
                identity_enabled=config.identity.enabled,
                character_name=config.identity.character_name,
                reference_policy=config.identity.reference_policy,
            )
            self._runtime = PluginRuntime(
                service,
                Delivery(
                    self.ctx.send,
                    include_tool_media=config.behavior.include_tool_media,
                    show_final_prompt=config.prompt.show_final_prompt,
                ),
                max_concurrency=config.behavior.max_concurrency,
                skip_when_busy=config.behavior.skip_when_busy,
                dedupe_seconds=config.behavior.dedupe_seconds,
                image_cache=image_cache,
                identity_store=identity_store,
                api_key_configured=bool(api_key),
                max_input_bytes=config.network.max_input_bytes,
            )
            self._intent_detector = IntentDetector(
                self.ctx.llm,
                planner_model=config.auto_trigger.planner_model,
                semantic_threshold=config.auto_trigger.semantic_threshold,
                bot_aliases=config.identity.bot_aliases,
                character_name=config.identity.character_name,
            )
            self._reply_gate = ReplyGate(
                self.ctx.message,
                timeout_seconds=config.auto_trigger.reply_wait_seconds,
            )
            self._coordinator = TriggerCoordinator(
                self._runtime,
                self._send_text_error,
                max_pending=config.auto_trigger.max_pending_per_chat,
                dedupe_seconds=config.auto_trigger.dedupe_seconds,
            )
        except Exception:
            await self._close_runtime()
            raise
        if not api_key:
            self.ctx.logger.warning(
                "未配置图片 API Key；请设置 %s 或在插件配置中填写 api.api_key",
                config.api.api_key_env,
            )

    async def _close_runtime(self) -> None:
        coordinator = self._coordinator
        runtime = self._runtime
        http = self._http
        self._coordinator = None
        self._runtime = None
        self._http = None
        self._intent_detector = None
        self._reply_gate = None

        first_error: Exception | None = None
        closers = (
            ("coordinator", coordinator.close if coordinator is not None else None),
            ("runtime", runtime.close if runtime is not None else None),
            ("HTTP", http.aclose if http is not None else None),
        )
        for stage, close in closers:
            if close is None:
                continue
            try:
                await close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                self._log_close_error(stage)
        if first_error is not None:
            raise first_error

    def _log_close_error(self, stage: str) -> None:
        try:
            self.ctx.logger.exception("关闭图片插件 %s 资源失败", stage)
        except Exception:
            pass

    async def _send_text_error(self, content: str, stream_id: str) -> Any:
        return await self.ctx.send.text(content, stream_id)


def create_plugin() -> ContextImagePlugin:
    return ContextImagePlugin()
