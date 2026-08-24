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

command -v codex >/dev/null 2>&1 || {
  printf 'codex is missing; install @openai/codex@0.148.0 first\n' >&2
  exit 1
}
observed_version=$(codex --version 2>/dev/null || true)
if [[ "$observed_version" != *0.148.0* ]]; then
  printf 'WARNING: audited Codex version is 0.148.0; observed: %s\n' \
    "${observed_version:-unknown}" >&2
fi

client_state_dir="$RUNTIME_ROOT/clients/codex"
mkdir -p "$client_state_dir"
if [[ ! -f "$client_state_dir/config.toml" ]]; then
  cp "$R1_DIR/clients/codex/config.toml.example" "$client_state_dir/config.toml"
fi
export CODEX_HOME="$client_state_dir"
export DSV4_API_KEY=${DSV4_API_KEY:-unused-local-key}
exec codex "$@"
