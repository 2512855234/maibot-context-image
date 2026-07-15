"""Safe exception types exposed by the plugin."""

from __future__ import annotations


class ContextImageError(Exception):
    """Base error with a message safe to return to chat users."""

    def __init__(self, public_message: str) -> None:
        self.public_message = public_message
        super().__init__(public_message)


class ConfigurationError(ContextImageError):
    pass


class IdentityRequiredError(ContextImageError):
    def __init__(self, message: str = "Bot 固定身份图尚未配置，无法生成一致的人物形象。") -> None:
        super().__init__(message)


class ImageSourceRequiredError(ContextImageError):
    def __init__(self, message: str = "没有找到可用于图生图的基础图片，请重新发送或回复一张图片。") -> None:
        super().__init__(message)


class PromptCompileError(ContextImageError):
    pass


class BusyError(ContextImageError):
    def __init__(self) -> None:
        super().__init__("当前会话已有图片生成任务，请稍后再试。")


class QueueFullError(ContextImageError):
    def __init__(
        self,
        message: str = "图片任务队列已满，请等待前面的图片生成完成。",
    ) -> None:
        super().__init__(message)


class DeliveryError(ContextImageError):
    def __init__(self) -> None:
        super().__init__("图片已生成，但发送失败，请稍后重试。")


class UpstreamError(ContextImageError):
    def __init__(
        self,
        public_message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(public_message)
