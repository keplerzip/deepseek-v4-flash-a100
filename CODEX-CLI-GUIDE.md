# Codex CLI 优先接入指南

本部署当前优先支持 Codex CLI。推荐链路是：

```text
Codex CLI -> New API 的 OpenAI/Responses 接口 -> DeepSeek-V4-Flash-0731
```

服务端生产配置和启动命令见 [README](README.md) 与
[生产运行文档](docs/OPERATIONS.md)。当前统一模型名为
`deepseek-v4-flash-0731-target`，服务端最大上下文为 256K。

目标机现场验证表明，这条链路能够正常完成多轮工具调用，并能使用本服务配置的
256K 上下文。Claude Code 经 New API 做 Anthropic/OpenAI 协议转换时，可能在工具调用
中途结束，因此不作为当前推荐客户端。

本指南中的清理操作只移出 Claude 的客户端配置和凭据。它不会删除
`~/.claude/projects/`、`~/.claude/history.jsonl` 或其他会话历史。

## 1. 一键生成 Codex CLI 独立配置

先确认已经安装 `codex`，然后在本部署目录执行下面整段命令。代码块右上角可以直接
复制：

```bash
./scripts/configure_codex_client.sh
```

脚本会交互询问：

- New API 地址，例如 `https://newapi.example.com`；
- New API Token，输入时不会回显；
- New API 中暴露的模型名，默认
  `deepseek-v4-flash-0731-target`；
- 上下文长度，默认 `262144`。

它会创建：

```text
~/.codex/dsv4.config.toml
~/.config/dsv4-codex/api-key
~/.local/bin/codex-dsv4
```

API Token 文件权限固定为 `0600`。配置使用独立的 `dsv4` profile，不覆盖
`~/.codex/config.toml`，也不会删除 Codex 的会话历史。官方 Codex 配置项使用
Responses 协议：

```toml
model = "deepseek-v4-flash-0731-target"
model_provider = "dsv4_newapi"
model_context_window = 262144

[model_providers.dsv4_newapi]
name = "DeepSeek V4 Flash 0731 via New API"
base_url = "https://newapi.example.com/v1"
env_key = "DSV4_NEWAPI_KEY"
wire_api = "responses"
```

配置完成后启动：

```bash
~/.local/bin/codex-dsv4
```

若 `~/.local/bin` 已在 `PATH` 中，也可以直接执行：

```bash
codex-dsv4
```

非交互配置可使用环境变量；Token 不会写入 TOML：

```bash
NEWAPI_BASE_URL='https://newapi.example.com' \
NEWAPI_API_KEY='请替换为你的Token' \
CODEX_MODEL='deepseek-v4-flash-0731-target' \
CODEX_CONTEXT_WINDOW=262144 \
./scripts/configure_codex_client.sh
```

注意：把 Token 直接写在命令行可能进入 shell 历史，日常使用优先选择交互模式。

## 2. 停用 Claude 配置，但保留全部历史

执行下面整段命令：

```bash
./scripts/retire_claude_client.sh
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL \
  ANTHROPIC_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL \
  ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
```

脚本不会使用 `rm -rf`。它只处理下列已知配置文件（存在时才移动）：

```text
~/.claude.json
~/.claude/settings.json
~/.claude/settings.local.json
~/.claude/.credentials.json
~/.claude/config.json
~/.claude/mcp.json
~/.config/claude/settings.json
~/.config/claude/config.json
~/.config/claude-code/settings.json
~/.config/claude-code/config.json
```

这些文件会移动到带时间戳的目录：

```text
~/.claude-config-backups/YYYYMMDDTHHMMSS/
```

以下历史路径明确不会移动或删除：

```text
~/.claude/projects/
~/.claude/history.jsonl
~/.claude/file-history/
~/.claude/session-env/
~/.claude/todos/
```

脚本会在备份目录中生成 `PRESERVED-HISTORY.txt`，记录清理前后发现的历史文件数量，
但不复制或读取会话正文。

检查历史仍然存在：

```bash
find "$HOME/.claude/projects" -type f -name '*.jsonl' 2>/dev/null | wc -l
test -f "$HOME/.claude/history.jsonl" && echo 'Claude history.jsonl preserved'
```

如果 shell 启动文件仍写有旧的 Claude/New API 环境变量，脚本会报告文件名和行号，
但不会擅自改写你的 shell 配置。手动删除对应 `ANTHROPIC_*` 行后重新打开终端即可。

CC Switch 等工具可能再次生成 Claude 配置；如果仍在使用这些工具，需要同时停用其中
的 Claude provider。

## 3. 恢复 Claude 配置

历史从未被移动。若还要恢复配置，将脚本输出的备份目录传入：

```bash
./scripts/retire_claude_client.sh --restore \
  "$HOME/.claude-config-backups/YYYYMMDDTHHMMSS"
```

恢复时如果目标文件已经存在，脚本会拒绝覆盖并列出冲突。

## 4. 快速确认 Codex 使用了正确模型

进入任意测试目录后启动：

```bash
codex-dsv4
```

先让 Codex 完成一个包含“读取文件、修改文件、运行检查”的小任务。然后在 New API
后台确认请求模型为 `deepseek-v4-flash-0731-target`，接口为 Responses/OpenAI 链路。
不要把 Claude Code 中途停止的现象归因于 TP、KV cache 或
`max_num_batched_tokens`；这些服务端参数不会把输出固定截断为 7 token。

## 5. 当前支持状态

| 客户端 | 状态 | 推荐接口 |
|---|---|---|
| Codex CLI | 优先支持，目标机已完成实际多轮使用 | OpenAI Responses |
| OpenAI 兼容客户端 | 支持 | Chat Completions / Responses |
| Claude Code 经 New API 格式转换 | 兼容性受限 | 不建议用于长工具任务 |
| Claude Code 原生 Anthropic 透传 | 尚需完整工具闭环验收 | `/v1/messages` |

Codex 自定义 provider 的用户级配置位于 `~/.codex`；本指南使用独立 profile 和
环境变量读取 Token，避免把密钥直接写进 `config.toml`。
