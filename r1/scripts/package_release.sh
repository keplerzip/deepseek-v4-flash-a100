#!/usr/bin/env bash
set -euo pipefail

# Developer-side reproducible packaging.  The target does not need Git.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd)
PROJECT_NAME=$(basename -- "$ROOT_DIR")
default_output="$(dirname -- "$ROOT_DIR")/$PROJECT_NAME.tar.gz"
output=${1:-$default_output}

command -v git >/dev/null 2>&1 || {
  printf 'ERROR: git is required to create the source delivery\n' >&2
  exit 1
}
command -v gzip >/dev/null 2>&1 || {
  printf 'ERROR: gzip is required to create the source delivery\n' >&2
  exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
  printf 'ERROR: sha256sum is required to create the checksum\n' >&2
  exit 1
}

git -C "$ROOT_DIR" diff --quiet
git -C "$ROOT_DIR" diff --cached --quiet
untracked=$(git -C "$ROOT_DIR" ls-files --others --exclude-standard)
[[ -z "$untracked" ]] || {
  printf 'ERROR: untracked release files must be reviewed and committed:\n%s\n' \
    "$untracked" >&2
  exit 1
}
head=$(git -C "$ROOT_DIR" rev-parse HEAD)
base=12810046c799cbe874967e19b1c0fa134ab7b209
git -C "$ROOT_DIR" merge-base --is-ancestor "$base" "$head"

if [[ -e "$output" && "${DSV4_PACKAGE_OVERWRITE:-0}" != 1 ]]; then
  printf 'ERROR: output already exists; set DSV4_PACKAGE_OVERWRITE=1: %s\n' \
    "$output" >&2
  exit 1
fi
output_dir=$(cd -- "$(dirname -- "$output")" && pwd)
output="$output_dir/$(basename -- "$output")"
temporary_tar=$(mktemp "${TMPDIR:-/tmp}/dsv4-delivery.XXXXXX.tar")
temporary_gz=$(mktemp "$output_dir/.${PROJECT_NAME}.XXXXXX.tar.gz")
cleanup() {
  rm -f -- "$temporary_tar" "$temporary_gz"
}
trap cleanup EXIT

git -C "$ROOT_DIR" archive \
  --format=tar \
  --prefix="$PROJECT_NAME/" \
  --add-virtual-file="$PROJECT_NAME/r1/manifests/delivery-git-sha.txt:$head" \
  --output="$temporary_tar" \
  "$head"
gzip -n -9 -c "$temporary_tar" >"$temporary_gz"
mv -f -- "$temporary_gz" "$output"
(
  cd -- "$output_dir"
  sha256sum "$(basename -- "$output")" \
    >"$(basename -- "$output").sha256"
)
printf 'DELIVERY_PACKAGE=PASS\narchive=%s\ncommit=%s\nchecksum=%s.sha256\n' \
  "$output" "$head" "$output"
