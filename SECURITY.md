# Security policy

## 不要提交敏感信息

本插件的 `config.toml` 仅作为无密钥配置模板发布。请通过环境变量或 MaiBot WebUI 配置实际凭据，并确认以下内容不会进入 Git：

- API Key、Bearer Token、GitHub Token 或其他访问令牌
- 私有 API 端点、账号密码和本地运行配置
- 身份照片、聊天缓存、生成图片和配置备份

推荐使用 `OPENAI_API_KEY` 环境变量。提交前请检查 `git diff --cached`，并运行项目测试；如果凭据曾经进入 Git 历史，即使后来删除也应立即撤销并重新生成。

## 报告问题

请在 GitHub Issues 中报告不包含凭据或私人图片的安全问题；不要在公开 Issue、Pull Request 或聊天消息中粘贴密钥、令牌或完整上游响应。
