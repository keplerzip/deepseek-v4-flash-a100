#!/usr/bin/env bash
set -euo pipefail

# Run the exact source-test paths requested by the release contract against
# the installed R1 package.  No host Python and no network are used.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_runtime_dirs
init_docker
docker_cmd image inspect "$SOURCE_TEST_IMAGE" >/dev/null 2>&1 || die \
  "source-test image is missing: $SOURCE_TEST_IMAGE"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$RESULT_DIR/source-tests-$timestamp"
mkdir -p "$run_dir"
host_user="$(id -u):$(id -g)"
overall_status=0

run_pytest() {
  local suite=$1
  local junit=$2
  shift 2
  set +e
  docker_cmd run --rm --network none --gpus all \
    --user "$host_user" \
    --tmpfs /tmp:rw,size=4g,exec \
    --volume "$ROOT_DIR/tests:/test-root/tests:ro" \
    --volume "$R1_DIR:/audit:ro" \
    --volume "$run_dir:/results:rw" \
    --workdir /test-root \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --env "CUDA_VISIBLE_DEVICES=$GPU_DEVICES" \
    --entrypoint python3 "$SOURCE_TEST_IMAGE" \
    -m pytest -q -p no:cacheprovider \
    --junitxml "/results/$junit" "$@" \
    2>&1 | tee "$run_dir/$suite.log"
  local observed=${PIPESTATUS[0]}
  set -e
  printf '%s\n' "$observed" >"$run_dir/$suite.exit-code"
  ((observed == 0)) || overall_status=1
}

run_pytest deepseek_v4_parser deepseek-v4-parser.xml \
  tests/parser/engine/test_deepseek_v4.py
run_pytest parser_engine parser-engine.xml tests/parser/engine
run_pytest deepseek_v4_tokenizer deepseek-v4-tokenizer.xml \
  tests/tokenizers_/test_deepseek_v4.py
run_pytest deepseek_v4_lifecycle deepseek-v4-lifecycle.xml \
  tests/models/test_deepseek_v4_target_post_load_contract.py
run_pytest deepseek_v4_mega_moe deepseek-v4-mega-moe.xml \
  tests/models/test_deepseek_v4_mega_moe.py
run_pytest anthropic_conversion anthropic-conversion.xml \
  tests/entrypoints/anthropic/test_anthropic_messages_conversion.py

set +e
docker_cmd run --rm --network none \
  --user "$host_user" \
  --volume "$R1_DIR:/audit:ro" \
  --volume "$run_dir:/results:rw" \
  --entrypoint python3 "$SOURCE_TEST_IMAGE" \
  /audit/tests/summarize_source_tests.py \
  --results-dir /results --output /results/summary.json
summary_status=$?
set -e
if ((overall_status != 0 || summary_status != 0)); then
  die "required source tests failed; see $run_dir"
fi
printf 'SOURCE_TESTS=PASS\nsummary=%s\n' "$run_dir/summary.json"
