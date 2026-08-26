#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
"$R2_DIR/scripts/load_image.sh"
if container_running "$CONTAINER_NAME"; then
  assert_owned_container "$CONTAINER_NAME"
  http_ready || die 'service is running but not API-ready'
else
  "$R2_DIR/scripts/start.sh"
fi
mkdir -p "$RESULT_DIR/acceptance"
args=(
  --rm --network bridge
  --add-host host.docker.internal:host-gateway
  --user "$(id -u):$(id -g)"
  --volume "$R2_DIR:/r2:ro"
  --entrypoint python3
)
[[ -z "${DSV4_API_KEY:-}" ]] || args+=(--env "DSV4_API_KEY=$DSV4_API_KEY")
docker_cmd run "${args[@]}" "$R2_IMAGE" /r2/tests/api_contract.py \
  >"$RESULT_DIR/acceptance/api-contract.json"
docker_cmd logs --timestamps "$CONTAINER_NAME" \
  >"$LOG_DIR/acceptance-server.log" 2>&1 || true
if grep -Eiq 'CUDA illegal memory|device-side assert|Traceback \(most recent call last\)|EngineCore.*died|OOM' \
  "$LOG_DIR/acceptance-server.log"; then
  die "fatal server signature found after acceptance; inspect $LOG_DIR/acceptance-server.log"
fi
printf 'ACCEPTANCE=PASS\nevidence=%s\n' "$RESULT_DIR/acceptance/api-contract.json"
