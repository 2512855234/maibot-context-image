"""Strongly typed plugin configuration with MaiBot WebUI metadata."""

from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase

from .prompt_defaults import (
    DEFAULT_EVERYDAY_PHOTO_COMPOSITION,
    DEFAULT_EVERYDAY_PHOTO_LIGHTING,
    DEFAULT_EVERYDAY_PHOTO_NEGATIVE,
    DEFAULT_EVERYDAY_PHOTO_STYLE,
)


def _ui(label: str, **metadata: object) -> dict[str, object]:
    """Build display-only metadata for MaiBot's generated plugin settings UI."""

    return {"label": label, **metadata}


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件开关"

    enabled: bool = Field(
        default=True,
        description="控制整个上下文生图插件是否启用。关闭后不会注册或处理本插件的生图能力。",
        json_schema_extra=_ui("启用插件"),
    )
    enable_tool: bool = Field(
        default=True,
        description="允许 MaiBot 在判断需要图片成品时调用 generate_image 生图工具。",
        json_schema_extra=_ui("注册生图工具"),
    )
    enable_commands: bool = Field(
        default=True,
        description="启用 /生图、/画图、/图生图、/改图 和 /image status 等私聊命令。",
        json_schema_extra=_ui("注册图片命令"),
    )
    config_version: str = Field(
        default="1.1.0",
        description="插件配置结构版本，供配置迁移使用；通常不需要手动修改。",
        json_schema_extra=_ui("配置结构版本"),
    )


class ApiSection(PluginConfigBase):
    __ui_label__ = "图片 API"

    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="兼容 OpenAI Images API 的服务根地址，不要在末尾填写具体接口路径。",
        json_schema_extra=_ui("API 服务地址"),
    )
    api_key: str = Field(
        default="",
        description="图片服务的 API Key。优先建议通过下方环境变量配置，避免明文保存。",
        json_schema_extra=_ui("API Key", **{"x-widget": "password"}),
    )
    api_key_env: str = Field(
        default="OPENAI_API_KEY",
        description="读取 API Key 的环境变量名称；配置文件中的 API Key 非空时优先使用配置值。",
        json_schema_extra=_ui("API Key 环境变量名"),
    )
    model: str = Field(
        default="gpt-image-2",
        description="调用图片 API 时提交的模型名称，需与所用服务商支持的模型一致。",
        json_schema_extra=_ui("图片模型"),
    )
    generations_path: str = Field(
        default="/images/generations",
        description="文生图接口相对于 API 服务地址的路径。",
        json_schema_extra=_ui("文生图接口路径"),
    )
    edits_path: str = Field(
        default="/images/edits",
        description="身份参考图、图生图和图片编辑接口相对于 API 服务地址的路径。",
        json_schema_extra=_ui("图片编辑接口路径"),
    )
    timeout_seconds: float = Field(
        default=300.0,
        description="单次图片 API 请求的最长等待时间，单位为秒。",
        json_schema_extra=_ui("请求超时（秒）"),
    )


class IdentitySection(PluginConfigBase):
    __ui_label__ = "Bot 固定身份"

    enabled: bool = Field(
        default=True,
        description="为 Bot 本人的图片使用固定身份参考图，以降低人物面容漂移。",
        json_schema_extra=_ui("启用固定身份"),
    )
    character_name: str = Field(
        default="麦麦",
        description="写入最终身份提示词的 Bot 规范角色名。",
        json_schema_extra=_ui("Bot 角色名"),
    )
    reference_policy: str = Field(
        default="fixed_only",
        description="身份参考策略。当前仅支持 fixed_only，表示始终从固定身份图开始生成。",
        json_schema_extra=_ui("身份参考策略"),
    )
    reference_filename: str = Field(
        default="bot-face.png",
        description="插件身份数据目录中的参考图片文件名；只能填写文件名，不能填写绝对路径。",
        json_schema_extra=_ui("固定身份图片文件名"),
    )
    force_for_bot_images: bool = Field(
        default=True,
        description="识别为 Bot 本人的图片请求必须使用固定身份参考图。",
        json_schema_extra=_ui("Bot 图片强制使用身份图"),
    )
    fail_if_missing: bool = Field(
        default=True,
        description="固定身份图缺失或不可读时终止 Bot 人物任务，避免无参考图生成陌生面容。",
        json_schema_extra=_ui("身份图缺失时终止"),
    )
    bot_aliases: list[str] = Field(
        default_factory=lambda: ["你", "麦麦", "MaiBot", "bot"],
        description="用于从用户请求中识别 Bot 本人的称呼；建议保留“你”和实际角色名。",
        json_schema_extra=_ui("Bot 称呼别名", **{"x-widget": "tags"}),
    )
    lock_face: bool = Field(
        default=True,
        description="在身份提示词中要求保持参考图的面部核心特征。",
        json_schema_extra=_ui("锁定面部特征"),
    )
    lock_hair: bool = Field(
        default=True,
        description="在身份提示词中要求保持参考图的基础发型和发色。",
        json_schema_extra=_ui("锁定发型发色"),
    )
    lock_eye_color: bool = Field(
        default=True,
        description="在身份提示词中要求保持参考图的眼睛颜色。",
        json_schema_extra=_ui("锁定眼睛颜色"),
    )
    lock_apparent_age: bool = Field(
        default=True,
        description="在身份提示词中要求保持参考人物的外观年龄范围。",
        json_schema_extra=_ui("锁定外观年龄"),
    )


class AutoTriggerSection(PluginConfigBase):
    __ui_label__ = "私聊自动生图"

    enabled: bool = Field(
        default=True,
        description="在私聊中识别自然语言生图或改图请求，并自动创建图片任务。",
        json_schema_extra=_ui("启用自然语言自动生图"),
    )
    planner_model: str = Field(
        default="planner",
        description="用于判断模糊图片意图的 MaiBot 模型任务名，不是具体模型名称。",
        json_schema_extra=_ui("意图判断模型任务"),
    )
    semantic_threshold: float = Field(
        default=0.80,
        description="Planner 判断结果达到该置信度才会触发生图；数值越高越保守。",
        json_schema_extra=_ui("语义触发置信度"),
    )
    max_pending_per_chat: int = Field(
        default=2,
        description="每个私聊允许等待执行的最大图片任务数，不包含正在执行的任务。",
        json_schema_extra=_ui("单个会话最大排队数"),
    )
    reply_wait_seconds: float = Field(
        default=12.0,
        description="自动生图发送前等待 MaiBot 正常文字回复的最长时间，单位为秒。",
        json_schema_extra=_ui("等待文字回复（秒）"),
    )
    dedupe_seconds: float = Field(
        default=90.0,
        description="同一私聊中重复自然语言请求的去重时间窗口，单位为秒。",
        json_schema_extra=_ui("自动触发去重时间（秒）"),
    )


class ContextSection(PluginConfigBase):
    __ui_label__ = "聊天上下文"

    enabled: bool = Field(
        default=True,
        description="编译图片提示词时读取当前私聊的近期聊天上下文。",
        json_schema_extra=_ui("启用聊天上下文"),
    )
    message_limit: int = Field(
        default=16,
        description="每次最多读取的近期消息条数。",
        json_schema_extra=_ui("最多读取消息数"),
    )
    max_chars: int = Field(
        default=8000,
        description="发送给提示词编译器的聊天上下文最大字符数，超出部分会被裁剪。",
        json_schema_extra=_ui("上下文最大字符数"),
    )
    hours: float = Field(
        default=4.0,
        description="只读取最近多少小时内的聊天消息。",
        json_schema_extra=_ui("上下文时间范围（小时）"),
    )
    include_reply: bool = Field(
        default=True,
        description="在可用时保留消息之间的回复引用关系，帮助理解指代对象。",
        json_schema_extra=_ui("包含回复引用"),
    )
    include_previous_prompt: bool = Field(
        default=True,
        description="允许上一张图片的最终提示词以文本形式参与场景连续性判断。",
        json_schema_extra=_ui("包含上一张图片提示词"),
    )
    include_image_descriptions: bool = Field(
        default=True,
        description="上下文中已有图片描述可用时，将其一并提供给提示词编译器。",
        json_schema_extra=_ui("包含历史图片描述"),
    )


class PromptSection(PluginConfigBase):
    __ui_label__ = "提示词编译"

    model: str = Field(
        default="planner",
        description="用于编译最终图片提示词的 MaiBot 模型任务名，默认跟随 Planner 配置。",
        json_schema_extra=_ui("提示词编译模型任务"),
    )
    temperature: float = Field(
        default=0.2,
        description="提示词编译模型的采样温度；较低数值通常更稳定。",
        json_schema_extra=_ui("编译温度"),
    )
    max_tokens: int = Field(
        default=1500,
        description="提示词编译模型单次响应允许生成的最大 Token 数。",
        json_schema_extra=_ui("编译最大 Token 数"),
    )
    join_mode: str = Field(
        default="smart_merge",
        description="上下文和当前请求的合并方式。当前建议保持 smart_merge。",
        json_schema_extra=_ui("提示词合并方式"),
    )
    language: str = Field(
        default="zh-CN",
        description="提示词编译器使用的语言区域标识。",
        json_schema_extra=_ui("提示词语言"),
    )
    show_final_prompt: bool = Field(
        default=False,
        description="生成完成后是否向用户附带展示最终图片提示词。",
        json_schema_extra=_ui("向用户显示最终提示词"),
    )
    fallback_to_template: bool = Field(
        default=True,
        description="LLM 编译失败时使用本地模板继续生成；关闭后编译失败会终止任务。",
        json_schema_extra=_ui("编译失败时使用本地模板"),
    )
    everyday_photo_defaults: bool = Field(
        default=True,
        description="未指定冲突画风时，自动合并自然、真实的日常随手拍视觉基线。",
        json_schema_extra=_ui("启用日常随手拍基线"),
    )
    default_style: str = Field(
        default=DEFAULT_EVERYDAY_PHOTO_STYLE,
        description="未指定动漫、插画、3D、棚拍等冲突画风时追加的默认摄影风格。",
        json_schema_extra=_ui("默认摄影风格", **{"x-widget": "textarea", "rows": 5}),
    )
    default_composition: str = Field(
        default=DEFAULT_EVERYDAY_PHOTO_COMPOSITION,
        description="日常拍照基线默认采用的镜头视角、主体位置和画面组织方式。",
        json_schema_extra=_ui("默认构图", **{"x-widget": "textarea", "rows": 4}),
    )
    default_lighting: str = Field(
        default=DEFAULT_EVERYDAY_PHOTO_LIGHTING,
        description="日常拍照基线默认采用的自然光、环境光、曝光和阴影要求。",
        json_schema_extra=_ui("默认光线", **{"x-widget": "textarea", "rows": 4}),
    )
    default_negative: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EVERYDAY_PHOTO_NEGATIVE),
        description="默认要求图片避免出现的视觉特征；每一项作为独立限制参与提示词编译。",
        json_schema_extra=_ui("默认避免项", **{"x-widget": "tags"}),
    )


class GenerationSection(PluginConfigBase):
    __ui_label__ = "图片生成参数"

    size: str = Field(
        default="1024x1536",
        description="请求图片模型输出的尺寸，例如 1024x1536。需确保所用服务支持该尺寸。",
        json_schema_extra=_ui("图片尺寸"),
    )
    quality: str = Field(
        default="high",
        description="图片质量档位，例如 low、medium、high；可用值取决于图片服务商。",
        json_schema_extra=_ui("图片质量"),
    )
    output_format: str = Field(
        default="png",
        description="请求的图片输出格式，例如 png、jpeg 或 webp；需由服务商支持。",
        json_schema_extra=_ui("输出格式"),
    )
    background: str = Field(
        default="auto",
        description="背景处理方式。auto 表示交由图片模型自动决定。",
        json_schema_extra=_ui("背景模式"),
    )
    moderation: str = Field(
        default="auto",
        description="图片服务的内容审核模式。auto 表示使用服务端默认策略。",
        json_schema_extra=_ui("内容审核模式"),
    )
    max_outputs: int = Field(
        default=1,
        description="单个任务允许返回并发送的最大图片数量。",
        json_schema_extra=_ui("单次最大输出图片数"),
    )
    max_reference_images: int = Field(
        default=2,
        description="图生图或身份生成时最多提交给图片 API 的参考图片数量。",
        json_schema_extra=_ui("最大参考图片数"),
    )


class BehaviorSection(PluginConfigBase):
    __ui_label__ = "运行行为"

    reference_image_max_age_seconds: int = Field(
        default=900,
        description="用户最近发送的图片可被明确改图请求使用的最长时间，单位为秒。",
        json_schema_extra=_ui("最近图片有效期（秒）"),
    )
    max_cached_images: int = Field(
        default=8,
        description="每个私聊在内存中最多保留的近期图片记录数量。",
        json_schema_extra=_ui("每个会话最大缓存图片数"),
    )
    max_concurrency: int = Field(
        default=2,
        description="插件全局同时执行的最大图片任务数量。",
        json_schema_extra=_ui("全局最大并发任务数"),
    )
    skip_when_busy: bool = Field(
        default=True,
        description="达到任务容量上限时直接拒绝新任务，避免继续堆积等待。",
        json_schema_extra=_ui("繁忙时拒绝新任务"),
    )
    dedupe_seconds: int = Field(
        default=90,
        description="命令、工具和自动触发之间识别重复图片任务的时间窗口，单位为秒。",
        json_schema_extra=_ui("任务去重时间（秒）"),
    )
    send_error_messages: bool = Field(
        default=True,
        description="任务失败时向当前私聊发送安全、简短的错误提示。",
        json_schema_extra=_ui("向用户发送错误提示"),
    )
    include_tool_media: bool = Field(
        default=True,
        description="处理工具调用时允许读取消息中附带的图片媒体。",
        json_schema_extra=_ui("工具调用包含图片媒体"),
    )


class NetworkSection(PluginConfigBase):
    __ui_label__ = "网络与安全"

    max_retries: int = Field(
        default=2,
        description="遇到可重试的网络错误或服务端错误时，最多额外重试的次数。",
        json_schema_extra=_ui("网络最大重试次数"),
    )
    max_input_bytes: int = Field(
        default=20 * 1024 * 1024,
        description="单张输入或参考图片允许的最大字节数。默认 20971520 字节，即 20 MiB。",
        json_schema_extra=_ui("输入图片大小上限（字节）"),
    )
    max_output_bytes: int = Field(
        default=50 * 1024 * 1024,
        description="单张 API 输出图片允许的最大字节数。默认 52428800 字节，即 50 MiB。",
        json_schema_extra=_ui("输出图片大小上限（字节）"),
    )
    verify_ssl: bool = Field(
        default=True,
        description="验证 HTTPS 服务端证书。除非调试可信的本地服务，否则不建议关闭。",
        json_schema_extra=_ui("验证 HTTPS 证书"),
    )
    block_private_networks: bool = Field(
        default=True,
        description="阻止插件从私有地址、回环地址或链路本地地址下载远程图片，以降低 SSRF 风险。",
        json_schema_extra=_ui("阻止访问私有网络"),
    )
    allowed_image_hosts: list[str] = Field(
        default_factory=list,
        description="允许下载远程图片的域名白名单。留空表示不额外限制公网域名；私网规则仍由上方开关控制。",
        json_schema_extra=_ui("远程图片域名白名单", **{"x-widget": "tags"}),
    )


class ContextImageConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    api: ApiSection = Field(default_factory=ApiSection)
    identity: IdentitySection = Field(default_factory=IdentitySection)
    auto_trigger: AutoTriggerSection = Field(default_factory=AutoTriggerSection)
    context: ContextSection = Field(default_factory=ContextSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    generation: GenerationSection = Field(default_factory=GenerationSection)
    behavior: BehaviorSection = Field(default_factory=BehaviorSection)
    network: NetworkSection = Field(default_factory=NetworkSection)
