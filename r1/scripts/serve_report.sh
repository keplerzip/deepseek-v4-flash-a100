#!/usr/bin/env bash
set -euo pipefail

# Serve a rebuilt or bundled self-contained report through the release image.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

init_docker
default_report_dir=$R1_DIR/reports
bundled_report=$R1_DIR/reports/$REPORT_BASENAME.html
if [[ -f "$RESULT_DIR/performance-$SCHEME_ID/performance-report.html" ]]; then
  default_report_dir=$RESULT_DIR/performance-$SCHEME_ID
fi
report_dir=${1:-$default_report_dir}
report_dir=$(cd -- "$report_dir" && pwd)
report_file=performance-report.html
if [[ "$report_dir" == "$R1_DIR/reports" ]]; then
  report_file=$(basename -- "$bundled_report")
fi
[[ -f "$report_dir/$report_file" ]] || die \
  "$report_file is missing from $report_dir; run ./benchmark_$SCHEME_ID.sh first"
report_container=$REPORT_CONTAINER_NAME
report_host=${REPORT_HOST:-127.0.0.1}
report_port=${REPORT_PORT:-$REPORT_PORT_DEFAULT}

if container_exists "$report_container"; then
  assert_owned_container "$report_container"
  container_running "$report_container" && die \
    "report container is already running: $report_container"
  docker_cmd container rm "$report_container" >/dev/null
fi

container_id=$(docker_cmd run --detach \
  --name "$report_container" \
  --label "com.deepseek.owner=$OWNER_LABEL" \
  --label "com.deepseek.release=$R1_RELEASE" \
  --label "com.deepseek.scheme=$SCHEME_ID" \
  --label com.deepseek.role=report \
  --network host \
  --mount "type=bind,src=$report_dir,dst=/report,readonly" \
  --workdir /report \
  --entrypoint python3 \
  "$R1_IMAGE" -m http.server "$report_port" --bind "$report_host")
printf 'REPORT_SERVER=PASS\nscheme=%s\ncontainer_id=%s\nurl=http://%s:%s/%s\n' \
  "$SCHEME_ID" "$container_id" "$report_host" "$report_port" "$report_file"
