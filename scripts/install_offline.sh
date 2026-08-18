#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_mode_config target-only
detect_runtime

install_mode=${INSTALL_MODE:-container}
case "$install_mode" in
  container)
    seed_archive="$ROOT_DIR/common/image/dsv4-a100-build-seed-f8ea5bb.tar"
    if [[ -s "$seed_archive" ]] && ! image_archive_path >/dev/null 2>&1; then
      "$ROOT_DIR/scripts/verify_target_build_seed.sh"
      "$ROOT_DIR/scripts/build_on_target_offline.sh"
      printf 'OFFLINE_INSTALL=PASS mode=target-compiled-container image=%s\n' "$IMAGE_NAME"
      exit 0
    fi
    "$ROOT_DIR/scripts/verify_offline_bundle.sh" --artifact-only
    if image_present; then
      revision=$(runtime image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE_NAME")
      [[ "$revision" == "$VLLM_COMMIT" ]] || die "existing image tag has the wrong revision"
      printf 'Image already present with the correct revision: %s\n' "$IMAGE_NAME"
    else
      archive=$(image_archive_path) || die "image archive not found"
      if [[ "$archive" == *.zst ]]; then
        command -v zstd >/dev/null 2>&1 || die "zstd is required to import $archive"
        zstd -q -dc "$archive" | runtime load
      else
        runtime load --input "$archive"
      fi
    fi
    "$ROOT_DIR/scripts/verify_image.sh"
    printf 'OFFLINE_INSTALL=PASS mode=container image=%s\n' "$IMAGE_NAME"
    ;;
  wheelhouse)
    marker="$ROOT_DIR/common/wheelhouse/WHEELHOUSE_COMPLETE"
    [[ -f "$marker" ]] || die "native wheelhouse is not marked complete"
    command -v python3.12 >/dev/null 2>&1 || die "Python 3.12 is required for native fallback"
    venv="$RUN_DIR/native-venv"
    python3.12 -m venv "$venv"
    "$venv/bin/python" -m pip install \
      --no-index \
      --find-links "$ROOT_DIR/common/wheelhouse" \
      --requirement "$ROOT_DIR/common/wheelhouse/requirements-lock.txt"
    printf 'OFFLINE_INSTALL=PASS mode=wheelhouse venv=%s\n' "$venv"
    ;;
  *) die "INSTALL_MODE must be container or wheelhouse" ;;
esac
