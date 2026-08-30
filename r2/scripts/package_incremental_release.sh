#!/usr/bin/env bash
set -euo pipefail

# Build-host only. Produces a small overlay delivery that reuses the exact R2
# image already loaded on the target.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
# shellcheck disable=SC1091
source "$R2_DIR/incremental/base.env"
init_docker
for command in awk git gzip mktemp sha256sum sort tar; do
  require_command "$command"
done

changed_vllm_files=(
  vllm/_version.py
  vllm/models/deepseek_v4/nvidia/flashinfer_sparse.py
  vllm/v1/worker/gpu/model_runner.py
)

[[ "$INCREMENTAL_RESULT_IMAGE" == "$R2_IMAGE" ]] || die \
  'incremental result tag must equal the R2.2 release image tag'
[[ "$(docker_cmd image inspect --format '{{.Id}}' "$INCREMENTAL_BASE_IMAGE")" == \
    "$INCREMENTAL_BASE_IMAGE_ID" ]] || die 'exact R2 base image is unavailable'
[[ "$(docker_cmd image inspect --format '{{.Id}}' "$R2_IMAGE")" == \
    'sha256:333503e39c788fb72cda4bbffc71deb3ab5338c08751c4c220a3be6bd24bda0a' ]] || die \
  'exact full R2.2 image is unavailable for overlay extraction'
git -C "$ROOT_DIR" diff --quiet
git -C "$ROOT_DIR" diff --cached --quiet
[[ -z "$(git -C "$ROOT_DIR" ls-files --others --exclude-standard)" ]] || die \
  'offline delivery repository has untracked files'

project_name=${INCREMENTAL_PROJECT_NAME:-deepseek-v4-flash-a100-r2.2-incremental-from-r2-20260830}
output=${1:-$(dirname -- "$ROOT_DIR")/$project_name.tar.gz}
output_dir=$(cd -- "$(dirname -- "$output")" && pwd)
output="$output_dir/$(basename -- "$output")"
[[ ! -e "$output" ]] || die "output already exists: $output"

staging=$(mktemp -d "${TMPDIR:-/tmp}/dsv4-r2-incremental.XXXXXX")
temporary=$(mktemp "$output_dir/.dsv4-r2-incremental.XXXXXX.tar.gz")
container_id=
cleanup() {
  [[ -z "$container_id" ]] || docker_cmd container rm "$container_id" >/dev/null 2>&1 || true
  rm -rf -- "$staging"
  rm -f -- "$temporary"
}
trap cleanup EXIT

base_manifest="$staging/base-vllm.sha256"
result_manifest="$staging/result-vllm.sha256"
docker_cmd run --rm --network none --entrypoint sh \
  "$INCREMENTAL_BASE_IMAGE" -c \
  'cd /usr/local/lib/python3.12/dist-packages && find vllm -type f -print0 | sort -z | xargs -0 sha256sum' \
  >"$base_manifest"
docker_cmd run --rm --network none --entrypoint sh "$R2_IMAGE" -c \
  'cd /usr/local/lib/python3.12/dist-packages && find vllm -type f -print0 | sort -z | xargs -0 sha256sum' \
  >"$result_manifest"
observed_diff=$(awk '
  NR == FNR { old[$2] = $1; next }
  {
    path = $2
    if (!(path in old)) print "ADDED " path
    else if (old[path] != $1) print "CHANGED " path
    seen[path] = 1
  }
  END {
    for (path in old) if (!(path in seen)) print "REMOVED " path
  }
' "$base_manifest" "$result_manifest" | sort)
expected_diff=$(printf 'CHANGED %s\n' "${changed_vllm_files[@]}" | sort)
[[ "$observed_diff" == "$expected_diff" ]] || {
  printf 'expected installed vLLM diff:\n%s\nobserved diff:\n%s\n' \
    "$expected_diff" "$observed_diff" >&2
  die 'R2-to-R2.2 installed vLLM diff is not fully represented by the overlay'
}

head=$(git -C "$ROOT_DIR" rev-parse HEAD)
epoch=$(git -C "$ROOT_DIR" log -1 --format=%ct HEAD)
delivery_paths=(
  LICENSE README.md START-HERE.md CHANGELOG.md VERSION THIRD_PARTY.md
  start.sh start_one.sh start_two.sh stop.sh
  status.sh status_one.sh status_two.sh
  benchmark_one.sh benchmark_two.sh
  report.sh report_one.sh report_two.sh run-tests.sh
  benchmark_cache_profiles.sh benchmark_dspark_k.sh update-from-r2.sh
  r2
)
git -C "$ROOT_DIR" archive --format=tar --prefix="$project_name/" "$head" \
  -- "${delivery_paths[@]}" | tar -xf - -C "$staging"

incremental_dir="$staging/$project_name/r2/incremental"
overlay_dir="$incremental_dir/overlay"
mkdir -p "$overlay_dir"
container_id=$(docker_cmd container create "$R2_IMAGE")
for relative_path in "${changed_vllm_files[@]}"; do
  destination="$overlay_dir/$relative_path"
  mkdir -p "$(dirname -- "$destination")"
  docker_cmd container cp \
    "$container_id:/usr/local/lib/python3.12/dist-packages/$relative_path" \
    "$destination"
done
docker_cmd container cp \
  "$container_id:/usr/local/lib/python3.12/dist-packages/$INCREMENTAL_NEW_DIST_INFO" \
  "$overlay_dir/"
docker_cmd container rm "$container_id" >/dev/null
container_id=

printf '%s\n' "${changed_vllm_files[@]}" \
  >"$incremental_dir/overlay-files.txt"

(cd -- "$incremental_dir" && \
  find Dockerfile overlay overlay-files.txt -type f -print0 | \
    sort -z | xargs -0 sha256sum \
    >payload.sha256)
payload_manifest_sha=$(sha256sum "$incremental_dir/payload.sha256" | awk '{print $1}')
{
  printf 'INCREMENTAL_PAYLOAD_MANIFEST_SHA256=%q\n' "$payload_manifest_sha"
  printf 'INCREMENTAL_DELIVERY_GIT_SHA=%q\n' "$head"
} >"$incremental_dir/manifest.env"

tar --sort=name --mtime="@$epoch" --owner=0 --group=0 --numeric-owner \
  -C "$staging" -cf - "$project_name" | gzip -n -9 >"$temporary"
mv -f -- "$temporary" "$output"
(cd -- "$output_dir" && sha256sum "$(basename -- "$output")" \
  >"$(basename -- "$output").sha256")
printf 'INCREMENTAL_DELIVERY=PASS\narchive=%s\nchecksum=%s.sha256\nbase_image_id=%s\npayload_manifest_sha256=%s\n' \
  "$output" "$output" "$INCREMENTAL_BASE_IMAGE_ID" "$payload_manifest_sha"
