# 兼容范围与版本锁

本交付不是自建代理，也没有把 Claude/OpenAI 协议互相翻译。请求直接进入固定
haosdent vLLM base 自带的原生端点：

- Codex：`/v1/responses`，`wire_api = "responses"`；
- OpenAI SDK：`/v1/chat/completions` 与 `/v1/responses`；
- Claude Code：`/v1/messages` 与 `/v1/messages/count_tokens`。

Claude Code 使用服务端 `/v1/models` discovery、custom-model fallback、
Anthropic gateway 环境变量和原生 Messages SSE。由于底层模型不是 Anthropic
Claude，这属于 best-effort 协议兼容，不能描述为 Anthropic 官方模型支持。

截至 2026-08-20 联网审计的客户端稳定版本为 Codex `0.148.0`、Claude Code
`2.1.237`。客户端 launcher 只警告版本漂移，不安装包，也不改用户原配置。
目标机即使没有这些 CLI，API contract 与稳定性测试仍会通过 Docker 直接验证
两套协议。

代码范围锁定在 `manifests/source-lock.json`：固定 base commit、四组最小语义
backport 及明确排除项均可审计。没有引入 stock/main、宽泛性能提交、DSpark、
MTP 或 speculative decoding。
