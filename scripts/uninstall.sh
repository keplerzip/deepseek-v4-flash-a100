#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
detect_runtime

for mode in target-only dspark; do
  # shellcheck disable=SC1090
  source "$ROOT_DIR/config/$mode.env"
  if container_exists "$CONTAINER_NAME"; then
    assert_owned_container "$CONTAINER_NAME" "$mode"
    "$ROOT_DIR/$mode/stop.sh"
  fi
done

if [[ "${REMOVE_IMAGE:-1}" == 1 ]] && image_present; then
  revision=$(runtime image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE_NAME")
  bundle=$(runtime image inspect --format '{{index .Config.Labels "com.deepseek.bundle"}}' "$IMAGE_NAME")
  [[ "$revision" == "$VLLM_COMMIT" && "$bundle" == "$BUNDLE_LABEL" ]] || \
    die "refusing to remove image: ownership/revision labels do not match"
  runtime image rm "$IMAGE_NAME"
  printf 'Removed only image %s; it can be restored from common/image/.\n' "$IMAGE_NAME"
fi

if [[ "${REMOVE_RUNTIME_CACHE:-0}" == 1 && -d "$RUN_DIR/cache" ]]; then
  rollback="$RUN_DIR/cache.uninstalled.$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$RUN_DIR/cache" "$rollback"
  printf 'Moved runtime cache to recoverable path: %s\n' "$rollback"
fi
if [[ -d "$RUN_DIR/native-venv" ]]; then
  rollback="$RUN_DIR/native-venv.uninstalled.$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$RUN_DIR/native-venv" "$rollback"
  printf 'Moved native venv to recoverable path: %s\n' "$rollback"
fi
printf 'Model weights, logs, configs, other containers and services were not modified.\n'
