# Codex and Claude Code clients

The server exposes one model under two names. Codex uses
`deepseek-v4-flash-0731-target` through the OpenAI Responses API. Claude Code
uses `claude-deepseek-v4-flash-0731-target` through the Anthropic Messages API;
the server returns that alias from `/v1/models`, and the launcher enables Claude
Code gateway model discovery plus a custom-model fallback.

Run the isolated launchers after the service acceptance tests pass:

```bash
r1/clients/codex/run_codex.sh
r1/clients/claude/run_claude.sh
```

The audited client versions on 2026-08-20 are Codex `0.148.0` and Claude Code
`2.1.237`. The launchers warn on version drift but do not install packages or
modify a user's existing client configuration. Codex state is kept below the
release `RUNTIME_ROOT`.

Claude Code support is protocol compatibility, not an Anthropic guarantee that
Claude Code supports a non-Claude model. The compatibility test covers model
discovery, token counting, non-streaming and streaming Messages requests, tool
use, and the trailing `system` turn emitted by Claude Code 2.1.207 and newer.
