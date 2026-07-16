# MaiBot Context Image

MaiBot 私聊智能生图插件。根据聊天上下文生成图片，并可使用固定人物参考照保持 Bot 形象一致。默认提示词偏向真实、自然的日常手机拍照效果。

## 主要功能

- **自然语言生图**：在私聊中说“给我拍一张海边的照片”等请求即可触发。
- **命令与 Tool**：支持 `/生图`、`/画图`、`/图生图`、`/改图` 和 `generate_image` Tool。
- **上下文理解**：结合近期聊天、回复内容和上一张图片提示词生成画面。
- **固定人物身份**：使用指定参考照约束 Bot 的面容、发型、瞳色和年龄感。
- **图生图与改图**：可编辑当前私聊中最近发送的图片。
- **日常拍照基线**：默认采用自然光、手机视角、生活化构图，减少影楼写真和商业广告感。
- **安全队列**：支持并发限制、私聊 FIFO、重复请求过滤和失败重试。
- **私聊限定**：群聊不会触发生图、缓存图片或调用图片 API。

## 使用示例

```text
给我拍一张你现在的照片
我想看你穿汉服的样子
拍一张海边散步的照片给我

/生图 一只戴红围巾的橘猫，写实摄影
/画图 一张雨夜城市插画
/图生图 把这张图改成下雪的夜晚
/改图 换成暖色灯光
/image status
```

图生图或改图前，请先在当前私聊发送一张图片。

## 安装

要求：MaiBot 1.0+、maibot-plugin-sdk 2.6+、Python 3.11+。

1. 将本仓库放入 MaiBot 的 `plugins/maibot-context-image/` 目录。
2. 在 MaiBot WebUI 的插件管理中启用插件。
3. 在插件设置中填写图片 API 参数。
4. 如需固定 Bot 形象，按 `[identity].reference_filename` 放置已授权的参考照。
5. 重载插件后，在私聊执行 `/image status` 检查状态。

> 建议通过 `OPENAI_API_KEY` 环境变量保存密钥。仓库中的 `config.toml` 是无密钥模板，请勿提交真实 API Key、Token、身份照片或配置备份。

## 核心参数

### 插件与 API

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `plugin.enabled` | `true` | 启用插件 |
| `plugin.enable_tool` | `true` | 启用 `generate_image` Tool |
| `plugin.enable_commands` | `true` | 启用生图命令 |
| `api.base_url` | `https://api.openai.com/v1` | OpenAI 兼容 API 地址 |
| `api.api_key` | 空 | API Key，建议改用环境变量 |
| `api.api_key_env` | `OPENAI_API_KEY` | API Key 环境变量名 |
| `api.model` | `gpt-image-2` | 图片生成模型 |
| `api.timeout_seconds` | `300` | 请求超时时间 |

### 人物身份

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `identity.enabled` | `true` | 启用固定人物身份 |
| `identity.character_name` | `麦麦` | Bot 的规范角色名 |
| `identity.bot_aliases` | `你、麦麦、MaiBot、bot` | 用于识别 Bot 主体的称呼 |
| `identity.reference_filename` | `bot-face.png` | 固定身份参考照文件名 |
| `identity.force_for_bot_images` | `true` | Bot 人物图强制使用参考照 |
| `identity.fail_if_missing` | `true` | 缺少参考照时停止人物生图 |
| `identity.lock_face` | `true` | 保持面容特征 |
| `identity.lock_hair` | `true` | 保持发型特征 |

默认身份照位置：

```text
data/plugins/com.maibot.context-image/identity/bot-face.png
```

仅使用本人所有或已获授权的照片。参考图会发送给所配置的图片服务商。

### 自动触发与上下文

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `auto_trigger.enabled` | `true` | 启用私聊自然语言触发 |
| `auto_trigger.planner_model` | `planner` | MaiBot 意图判断任务名 |
| `auto_trigger.semantic_threshold` | `0.80` | 语义触发阈值 |
| `auto_trigger.max_pending_per_chat` | `2` | 每个私聊最大等待任务数 |
| `auto_trigger.reply_wait_seconds` | `12` | 等待正常文字回复的时间 |
| `context.message_limit` | `16` | 读取的近期消息数量 |
| `context.max_chars` | `8000` | 上下文最大字符数 |
| `context.hours` | `4` | 上下文时间范围 |
| `context.include_previous_prompt` | `true` | 参考上一张图片提示词 |

### 提示词

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `prompt.model` | `planner` | MaiBot 提示词编译任务名 |
| `prompt.language` | `zh-CN` | 提示词语言 |
| `prompt.fallback_to_template` | `true` | LLM 编译失败时使用本地模板 |
| `prompt.everyday_photo_defaults` | `true` | 启用日常随手拍视觉基线 |
| `prompt.default_style` | 日常写实摄影 | 默认画面风格 |
| `prompt.default_composition` | 自然手机视角 | 默认构图基线 |
| `prompt.default_lighting` | 自然光或环境光 | 默认光线基线 |
| `prompt.default_negative` | 默认避免项列表 | 控制畸形、塑料皮肤、过度精修等问题 |

用户明确要求动漫、插画、3D、棚拍、广告大片或电影感时，插件会停用日常拍照基线，以用户指定风格为准。

### 图片输出与运行

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `generation.size` | `1024x1536` | 输出尺寸 |
| `generation.quality` | `high` | 图片质量 |
| `generation.output_format` | `png` | 输出格式 |
| `generation.max_reference_images` | `2` | 最大参考图数量 |
| `behavior.max_concurrency` | `2` | 全局最大并发数 |
| `behavior.reference_image_max_age_seconds` | `900` | 最近图片有效时间 |
| `behavior.max_cached_images` | `8` | 每个私聊缓存图片数量 |
| `network.max_retries` | `2` | 网络失败重试次数 |
| `network.max_input_bytes` | `20971520` | 单张输入图大小上限 |
| `network.block_private_networks` | `true` | 阻止访问私有网络地址 |

完整默认值请查看 [`config.toml`](config.toml)。

## 许可证

[Apache License 2.0](LICENSE)
