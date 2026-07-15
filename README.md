# MaiBot Context Image

基于实时聊天上下文、固定 Bot 身份参考照和 GPT Image 的 MaiBot 私聊生图插件。

## 私聊限定

插件的全部能力都只在私聊中工作，包括自然语言自动触发、`generate_image` Tool、`/生图`、`/画图`、`/图生图`、`/改图`、`/image status` 命令以及最近图片缓存。

群聊不会缓存图片、判断生图意图、进入任务队列、调用 Planner、编译 Prompt、调用图片 API，也不会运行本插件的命令或 Tool。SDK 组件的 `chat_scope="private"` 是主要分发边界；作为纵深防护，命令和 Tool 在收到明确的非空 `group_id` 时会拒绝，消息事件也会在缓存或分类前再次确认消息记录属于私聊。这里不假定缺少会话字段本身等同于已识别的群聊。

## 使用方式

在私聊中可以直接提出自然的图片请求，例如：

- `给我拍一张你现在的照片`
- `我想看你穿汉服的样子`
- `拍一张海边的照片给我`

也可以使用明确命令：

```text
/生图 一只戴红围巾的橘猫，写实摄影
/画图 一张雨夜城市插画
/图生图 把这张图改成下雪的夜晚
/改图 换成暖色灯光
/image status
```

图生图前请先在当前私聊发送一张图片。最近图片仅用于明确的编辑请求，不会自动成为 Bot 身份参考。

### 自然语言判断与发送顺序

明确的生成、自拍、穿搭或编辑请求先由确定性规则直接识别；否定请求、能力询问和普通讨论不会触发。只有可能表达视觉意图但含义不明确的候选消息才交给 `[auto_trigger].planner_model` 指定的 MaiBot Planner 任务判断。

`planner` 是 MaiBot 的模型任务名，不是插件硬编码的具体文本模型。MaiBot 会从当前 `model_task_config.planner` 解析实际模型；更换 Planner 模型时不需要在插件中固定某个具体模型名称。结构无效、低于阈值、超时或失败的 Planner 判断都会安全地视为“不触发”，不会调用图片 API。

自然语言触发不会拦截 MaiBot 的正常人格回复。图片可在后台生成；发送前，插件会观察原消息之后的 Bot 新回复，观察到时先保留正常文字回复、再发送图片。如果在 `[auto_trigger].reply_wait_seconds` 内未观察到回复，等待门会超时并继续发送图片，不会永久阻塞任务。命令和 Tool 请求不使用这个回复等待门。

## 固定 Bot 身份

`[identity]` 中的关键字段：

```toml
[identity]
character_name = "麦麦"
bot_aliases = ["你", "麦麦", "MaiBot", "bot"]
reference_filename = "bot-face.png"
reference_policy = "fixed_only"
```

- `character_name`：写入最终身份 Prompt 的规范角色名。
- `bot_aliases`：把用户对 Bot 的称呼识别为 `subject=bot`；建议保留“你”。
- `reference_filename`：身份目录内的固定参考图文件名，只能是文件名，不能是绝对路径或包含上级目录。
- `reference_policy="fixed_only"`：当前唯一支持的 Bot 身份策略。

每一次 `subject=bot` 生成都会重新从固定身份照开始，并把它作为面容的唯一权威图片来源。普通的后续生成绝不会拿上一张生成图充当身份图；场景连续性只可通过上一张最终 Prompt 的文本参与，当前请求中的服装、姿势、构图和场景优先覆盖历史文本。

明确编辑含 Bot 的旧图时，参考图职责和顺序固定：第一张是固定身份图，用来决定人物面容；第二张是待编辑基础图，只用来参考构图和场景。身份图缺失、不可读、格式不支持或超出大小限制时，Bot 人物任务会在图片 API 调用前失败，不会降级成无身份图的纯文生图。

身份参考可降低人物漂移，但图片模型不能保证每次都达到像素级一致。

### 身份照片准备与隐私

把已获得授权、主体清楚、正面可辨识的单人照片保存为 `reference_filename` 配置的文件名。支持 PNG、JPEG 和 WebP，大小不得超过 `[network].max_input_bytes`。默认位置为：

```text
data/plugins/com.maibot.context-image/identity/bot-face.png
```

身份照和明确编辑时使用的基础图会发送给所配置的图片服务商。请只使用本人所有或已明确获得授权的图片，并遵守服务商条款和当地隐私规定；不要将无授权的人像、敏感证件或私密图片放入身份目录或聊天。

## 队列、去重与失败处理

`[auto_trigger]` 配置自然语言入口：

```toml
[auto_trigger]
enabled = true
planner_model = "planner"
semantic_threshold = 0.80
max_pending_per_chat = 2
reply_wait_seconds = 12.0
dedupe_seconds = 90.0
```

- `enabled`：是否启用私聊自然语言自动触发；关闭后不影响私聊命令和 Tool 自身的入口开关。
- `planner_model`：用于模糊意图分类的 MaiBot 模型任务名。
- `semantic_threshold`：Planner 结果允许触发的最低置信度。
- `max_pending_per_chat`：每个私聊在 1 个活动任务之外可等待的任务数；默认值 `2` 表示最多 1 个活动任务加 2 个 FIFO 等待任务。
- `reply_wait_seconds`：自然语言任务发图前等待正常人格回复的最长时间。
- `dedupe_seconds`：队列层的重复请求时间窗口。

同一私聊严格按 FIFO 顺序处理；不同私聊可以并行，但仍受 `[behavior].max_concurrency` 的全局限制。消息事件、命令和 Tool 共用协调队列。去重优先使用来源消息 ID；没有消息 ID 时使用私聊流 ID、规范化请求和时间窗口。成功结果还有 `[behavior].dedupe_seconds` 保护，避免短时间重复生成。队列已满或重复提交时不会产生额外图片 API 调用。

不同失败阶段采用不同处理方式：

- Planner 调用失败、输出无效或低于阈值时静默按“不触发”处理，不进入队列，也不调用图片 API。
- 自然语言自动触发提交时如果队列已满，消息事件会捕获该安全异常并写入不含敏感细节的日志；该路径不保证向聊天发送提示。命令和 Tool 的同步提交失败则返回安全结果。
- 已进入后台队列的生成或发送任务如果抛出 `ContextImageError`，协调器通过安全错误发送器发送其公开消息；其他未预期异常统一发送通用失败消息。两者都不会暴露密钥、上游响应体或内部路径，错误发送自身失败也不会卡住 FIFO。

固定身份图缺失属于后台生成阶段的安全错误，并且会在图片 API 调用前失败。任何自动触发异常都不会阻塞正常文字聊天。插件卸载或配置热重载时会先停止接收旧队列的新任务，清除尚未开始的等待任务，等待已活动任务收尾，再依据新配置重建运行时；身份名称、别名和自动触发设置随后生效。

## Prompt 模型任务

`[prompt].model` 控制最终图片 Prompt 的文本编译，填写的同样是 MaiBot 模型任务名，默认值为 `planner`。插件会调用 MaiBot 当前 Planner 任务所配置的实际模型，不应在这里硬编码具体文本模型名称。

Prompt 编译失败时，插件会按 `[prompt].fallback_to_template` 回退到本地确定性模板。图片生成模型仍由 `[api].model` 独立配置。

### 默认“日常随手拍”视觉基线

默认开启 `[prompt].everyday_photo_defaults`，用于让没有明确指定画风的图片更接近日常手机拍照，而不是影楼写真、广告大片或电影剧照。它由四部分组成：

- **风格**：写实生活摄影，像朋友用手机随手拍；保留自然皮肤纹理、细小瑕疵、衣物褶皱和普通生活环境，不过度磨皮或精修。
- **构图**：普通手机/消费级相机视角，手持、自然、略有不完美，不刻意居中对称或摆拍。
- **光线**：使用现场自然光或普通环境光，真实曝光和白平衡，允许轻微明暗不均，避免影棚布光。
- **避免项**：文字、水印、面部/肢体畸变、多余手指、塑料皮肤、过度磨皮、HDR 过重、夸张虚化、影楼写真、商业广告感、CGI、插画感和刻意摆拍。

当前请求仍然优先。例如以下请求会保留具体场景信息，并自动加入日常拍照基线：

```text
给我拍一张你现在在窗边喝咖啡的照片
拍一张下班路上的随手自拍，光线有点暗
记录一下周末在菜市场买菜的样子
拍张海边照片，不要摆拍
```

如果用户明确要求 `动漫`、`插画`、`水彩`、`3D 渲染`、`棚拍`、`商业广告`、`时尚大片` 或 `电影感`，编译器会停用这套日常照片基线，不强行把请求改成手机照片。需要完全关闭时，在配置中设置：

```toml
[prompt]
everyday_photo_defaults = false
```

风格、构图、光线和避免项也可以分别通过 `[prompt].default_style`、`[prompt].default_composition`、`[prompt].default_lighting` 和 `[prompt].default_negative` 调整。配置热重载后对新请求生效。

## 安装与配置

要求 MaiBot 1.0+、maibot-plugin-sdk 2.6+、Python 3.11+。目录根部必须保留 `plugin.py`、`_manifest.json`、`config.toml` 和 `context_image/`。

1. MaiBot Desktop：将整个 `maibot-context-image` 目录放到 `<MaiBot Desktop 数据目录>/modules/MaiBot/plugins/maibot-context-image/`。
2. 手动安装：将整个目录放到 `<MaiBot 目录>/plugins/maibot-context-image/`。
3. 在 MaiBot WebUI 的“插件管理”中启用插件。
4. 在插件配置中确认 `[prompt].model` 和 `[auto_trigger].planner_model` 都填写可用的 MaiBot 任务名（默认 `planner`）。
5. 通过私有环境变量 `OPENAI_API_KEY` 或 WebUI 的 `[api].api_key` 配置图片服务凭据。示例 `config.toml` 中的 `api_key` 应保持为空；不要把真实密钥、访问令牌、私有端点或包含它们的配置文件提交到仓库或发到聊天中。
6. 按上一节放置已授权的固定身份照，然后重载插件，并在私聊执行 `/image status` 检查图片 API 与身份图状态。

不要用工作区示例 `config.toml` 覆盖已有 Desktop 配置；更新安装时应保留用户现有密钥、端点和其他设置。

## 私聊验收清单

- [ ] 私聊发送 `给我拍一张你现在的照片`：若在 `reply_wait_seconds` 内观察到正常人格回复，确认文字先于使用固定身份照生成的图片；若未观察到回复，确认图片在等待超时后仍继续发送而不是永久阻塞。
- [ ] 私聊发送 `我想看你穿汉服的样子`：仍从固定身份照开始，没有把上一张生成图当身份参考。
- [ ] 私聊发送 `拍一张海边的照片给我`：明确请求直接触发，不需要 Planner 分类。
- [ ] 快速发送三个不同请求：同一私聊保持 1 个活动任务和最多 2 个 FIFO 等待任务，图片按顺序发送。
- [ ] 重复提交同一来源消息或短时间重复请求：不会产生额外图片 API 调用。
- [ ] 明确编辑 Bot 旧图：日志或测试证据显示参考顺序为固定身份图第一、待编辑图第二。
- [ ] 缺少身份图、Planner 分类失败或图片 API 失败：正常聊天不被阻塞，提示中没有密钥、令牌、上游响应体或内部路径。

### 群聊负向检查

- [ ] 在群聊发送上述三条自然语言请求：不缓存、不分类、不排队、不调用 Planner 或图片 API。
- [ ] 在群聊调用 `/生图 测试`、`/画图 测试`、`/图生图 测试`、`/改图 测试` 和 `/image status`：命令不运行，不调用图片 API。
- [ ] 在群聊尝试 `generate_image` Tool：返回仅支持私聊的拒绝结果，不进入 Prompt 编译或生成流程。
- [ ] 检查相关日志：没有群聊触发的分类、队列、Prompt、身份图上传或图片 API 记录，也没有凭据或完整敏感上下文。

## 本地自动化测试

从插件目录运行完整测试；测试使用模拟 LLM 与 HTTP 传输，不会调用真实图片 API：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
& '<MaiBot Desktop Python 路径>\python.exe' -m unittest discover -s tests -v
```
