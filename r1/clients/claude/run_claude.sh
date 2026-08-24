#!/usr/bin/env bash
set -euo pipefail

R1_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
set -a
# shellcheck disable=SC1091
source "$R1_DIR/config/target.env"
if [[ -f "$R1_DIR/config/secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$R1_DIR/config/secrets.env"
fi
set +a

command -v claude >/dev/null 2>&1 || {
  printf 'claude is missing; install @anthropic-ai/claude-code@2.1.237 first\n' >&2
  exit 1
}
observed_version=$(claude --version 2>/dev/null || true)
if [[ "$observed_version" != *2.1.237* ]]; then
  printf 'WARNING: audited Claude Code version is 2.1.237; observed: %s\n' \
    "${observed_version:-unknown}" >&2
fi

unset ANTHROPIC_API_KEY
export ANTHROPIC_BASE_URL="http://$HOST:$PORT"
export ANTHROPIC_AUTH_TOKEN=${DSV4_API_KEY:-unused-local-key}
export ANTHROPIC_MODEL="$CLAUDE_MODEL_ALIAS"
export ANTHROPIC_DEFAULT_MODEL="$CLAUDE_MODEL_ALIAS"
export ANTHROPIC_DEFAULT_OPUS_MODEL="$CLAUDE_MODEL_ALIAS"
export ANTHROPIC_DEFAULT_SONNET_MODEL="$CLAUDE_MODEL_ALIAS"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="$CLAUDE_MODEL_ALIAS"
export ANTHROPIC_CUSTOM_MODEL_OPTION="$CLAUDE_MODEL_ALIAS"
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export API_TIMEOUT_MS=${API_TIMEOUT_MS:-1800000}

printf 'NOTICE: Claude Code is using best-effort Anthropic Messages API compatibility; '
printf 'Anthropic does not certify non-Claude models.\n' >&2
exec claude --model "$CLAUDE_MODEL_ALIAS" "$@"
