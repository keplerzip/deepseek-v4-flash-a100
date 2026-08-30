#!/usr/bin/env bash
set -euo pipefail

INCREMENTAL_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
R2_DIR=$(cd -- "$INCREMENTAL_DIR/.." && pwd)

# shellcheck disable=SC1091
source "$R2_DIR/scripts/lib.sh"
# shellcheck disable=SC1091
source "$INCREMENTAL_DIR/base.env"
[[ -f "$INCREMENTAL_DIR/manifest.env" ]] || die \
  'incremental manifest is missing; use the generated incremental delivery'
# shellcheck disable=SC1091
source "$INCREMENTAL_DIR/manifest.env"

require_command sha256sum
init_docker

[[ "$INCREMENTAL_RESULT_IMAGE" == "$R2_IMAGE" ]] || die \
  'incremental result image disagrees with the R2.3 release contract'
[[ "$INCREMENTAL_RESULT_SOURCE_COMMIT" == "$R2_SOURCE_COMMIT" ]] || die \
  'incremental source revision disagrees with the R2.3 release contract'
[[ "$INCREMENTAL_RELEASE" == "$R2_RELEASE" ]] || die \
  'incremental release identity disagrees with the R2.3 release contract'

(cd -- "$INCREMENTAL_DIR" && sha256sum -c payload.sha256 >/dev/null) || die \
  'incremental payload checksum verification failed'
observed_payload_manifest=$(sha256sum "$INCREMENTAL_DIR/payload.sha256" | awk '{print $1}')
[[ "$observed_payload_manifest" == "$INCREMENTAL_PAYLOAD_MANIFEST_SHA256" ]] || die \
  'incremental payload manifest checksum mismatch'

docker_cmd image inspect "$INCREMENTAL_BASE_IMAGE" >/dev/null 2>&1 || die \
  "exact R2 base image is missing: $INCREMENTAL_BASE_IMAGE"
observed_base_id=$(docker_cmd image inspect --format '{{.Id}}' "$INCREMENTAL_BASE_IMAGE")
[[ "$observed_base_id" == "$INCREMENTAL_BASE_IMAGE_ID" ]] || die \
  "R2 base image ID mismatch: expected=$INCREMENTAL_BASE_IMAGE_ID observed=$observed_base_id"
observed_base_revision=$(docker_cmd image inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
  "$INCREMENTAL_BASE_IMAGE")
[[ "$observed_base_revision" == "$INCREMENTAL_BASE_SOURCE_COMMIT" ]] || die \
  'R2 base image source revision mismatch'
observed_base_file=$(docker_cmd run --rm --network none --entrypoint sha256sum \
  "$INCREMENTAL_BASE_IMAGE" \
  /usr/local/lib/python3.12/dist-packages/vllm/models/deepseek_v4/nvidia/flashinfer_sparse.py \
  | awk '{print $1}')
[[ "$observed_base_file" == "$INCREMENTAL_BASE_FLASHINFER_SHA256" ]] || die \
  'R2 base runtime file checksum mismatch'
observed_base_responses=$(docker_cmd run --rm --network none --entrypoint sha256sum \
  "$INCREMENTAL_BASE_IMAGE" \
  /usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/responses/serving.py \
  | awk '{print $1}')
[[ "$observed_base_responses" == "$INCREMENTAL_BASE_RESPONSES_SERVING_SHA256" ]] || die \
  'R2 base Responses runtime checksum mismatch'

result_image=${DSV4_INCREMENTAL_TEST_IMAGE:-$INCREMENTAL_RESULT_IMAGE}
if docker_cmd image inspect "$result_image" >/dev/null 2>&1; then
  observed_revision=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$result_image")
  observed_payload=$(docker_cmd image inspect --format \
    '{{index .Config.Labels "com.deepseek.incremental.payload-manifest-sha256"}}' \
    "$result_image")
  if [[ "$observed_revision" == "$INCREMENTAL_RESULT_SOURCE_COMMIT" && \
        "$observed_payload" == "$INCREMENTAL_PAYLOAD_MANIFEST_SHA256" ]]; then
    log "exact incremental image is already present: $result_image"
  elif [[ "$result_image" == "$INCREMENTAL_RESULT_IMAGE" && \
          "$observed_revision" == "$INCREMENTAL_RESULT_SOURCE_COMMIT" ]]; then
    log "an exact full R2.3 image is already present: $result_image"
  else
    die "incremental result image tag is occupied by different content: $result_image"
  fi
else
  log "building the R2.3 overlay from the verified 2026-08-26 R2 base (offline, no compilation)"
  DOCKER_BUILDKIT=1 docker_cmd build \
    --network none \
    --pull=false \
    --no-cache \
    --build-arg "BASE_IMAGE=$INCREMENTAL_BASE_IMAGE" \
    --build-arg "RESULT_SOURCE_COMMIT=$INCREMENTAL_RESULT_SOURCE_COMMIT" \
    --build-arg "RELEASE=$INCREMENTAL_RELEASE" \
    --build-arg "BASE_IMAGE_ID=$INCREMENTAL_BASE_IMAGE_ID" \
    --build-arg "PAYLOAD_MANIFEST_SHA256=$INCREMENTAL_PAYLOAD_MANIFEST_SHA256" \
    --tag "$result_image" \
    --file "$INCREMENTAL_DIR/Dockerfile" \
    "$INCREMENTAL_DIR"
fi

[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$result_image")" == \
    "$INCREMENTAL_RESULT_SOURCE_COMMIT" ]] || die 'incremental image revision label mismatch'
[[ "$(docker_cmd image inspect --format '{{index .Config.Labels "com.deepseek.cuda.arch"}}' "$result_image")" == 8.0 ]] || die \
  'incremental image CUDA architecture label mismatch'

read -r result_flashinfer result_model_runner result_responses result_version < <(docker_cmd run --rm --network none \
  --entrypoint python3 "$result_image" -c '
from hashlib import sha256
from pathlib import Path
root=Path("/usr/local/lib/python3.12/dist-packages/vllm")
paths=("models/deepseek_v4/nvidia/flashinfer_sparse.py", "v1/worker/gpu/model_runner.py", "entrypoints/openai/responses/serving.py", "_version.py")
print(*(sha256((root/path).read_bytes()).hexdigest() for path in paths))
')
[[ "$result_flashinfer" == "$INCREMENTAL_RESULT_FLASHINFER_SHA256" ]] || die \
  'incremental result backend checksum mismatch'
[[ "$result_model_runner" == "$INCREMENTAL_RESULT_MODEL_RUNNER_SHA256" ]] || die \
  'incremental result GPU model runner checksum mismatch'
[[ "$result_responses" == "$INCREMENTAL_RESULT_RESPONSES_SERVING_SHA256" ]] || die \
  'incremental result Responses serving checksum mismatch'
[[ "$result_version" == "$INCREMENTAL_RESULT_VERSION_SHA256" ]] || die \
  'incremental result version checksum mismatch'
docker_cmd run --rm --network none \
  --volume "$R2_DIR:/audit:ro" \
  --entrypoint python3 "$result_image" \
  /audit/scripts/verify_runtime_source.py >/dev/null || die \
  'incremental image failed the DeepSeek V4 source contract'
observed_version=$(docker_cmd run --rm --network none --entrypoint python3 \
  "$result_image" -c 'import importlib.metadata, vllm; assert importlib.metadata.version("vllm") == vllm.__version__; print(vllm.__version__)')
[[ "$observed_version" == 0.1.dev43+gcf7898691.d20260830 ]] || die \
  "incremental vLLM version mismatch: $observed_version"

printf 'INCREMENTAL_IMAGE=PASS\nbase=%s\nbase_id=%s\nimage=%s\nimage_id=%s\nvllm=%s\nnetwork=none\ncompilation=none\n' \
  "$INCREMENTAL_BASE_IMAGE" "$INCREMENTAL_BASE_IMAGE_ID" "$result_image" \
  "$(docker_cmd image inspect --format '{{.Id}}' "$result_image")" "$observed_version"
