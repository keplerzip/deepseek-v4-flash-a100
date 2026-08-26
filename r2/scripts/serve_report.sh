#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
"$R2_DIR/scripts/load_image.sh"
report_dir="$RESULT_DIR/benchmark"
report_basename=${DSV4_REPORT_BASENAME:-long-context-matrix}
[[ "$report_basename" =~ ^[a-zA-Z0-9._-]+$ ]] || die 'unsafe report basename'
report_file="$report_dir/$report_basename.html"
[[ -f "$report_file" ]] || die "report does not exist yet: $report_file"
port=${DSV4_REPORT_PORT:-$REPORT_PORT_DEFAULT}
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1024 && port <= 65535)) || die \
  "invalid report port: $port"

if container_exists "$REPORT_CONTAINER_NAME"; then
  owner=$(docker_cmd inspect --format \
    '{{index .Config.Labels "com.deepseek.owner"}}' "$REPORT_CONTAINER_NAME" 2>/dev/null || true)
  [[ "$owner" == "$OWNER_LABEL" ]] || die \
    "report container name is occupied by another owner: $REPORT_CONTAINER_NAME"
  role=$(docker_cmd inspect --format \
    '{{index .Config.Labels "com.deepseek.role"}}' "$REPORT_CONTAINER_NAME")
  [[ "$role" == report ]] || die \
    "release-owned container is not a report server: $REPORT_CONTAINER_NAME"
  if container_running "$REPORT_CONTAINER_NAME"; then
    mounted_report_dir=$(docker_cmd inspect --format \
      '{{range .Mounts}}{{if eq .Destination "/report"}}{{.Source}}{{end}}{{end}}' \
      "$REPORT_CONTAINER_NAME")
    [[ "$mounted_report_dir" == "$report_dir" ]] || die \
      "running report server uses another result directory: $mounted_report_dir"
    published=$(docker_cmd port "$REPORT_CONTAINER_NAME" 8080/tcp)
    [[ "$published" != *$'\n'* ]] || die \
      "running report server has multiple bindings: $published"
    [[ "$published" =~ ^127[.]0[.]0[.]1:([0-9]+)$ ]] || die \
      "running report server has an unsafe or unknown binding: $published"
    actual_port=${BASH_REMATCH[1]}
    printf 'REPORT_SERVER=READY\nurl=http://127.0.0.1:%s/%s.html\ncontainer=%s\n' \
      "$actual_port" "$report_basename" "$REPORT_CONTAINER_NAME"
    exit 0
  fi
  docker_cmd rm "$REPORT_CONTAINER_NAME" >/dev/null
fi

docker_cmd run --detach --rm \
  --name "$REPORT_CONTAINER_NAME" \
  --label "com.deepseek.owner=$OWNER_LABEL" \
  --label "com.deepseek.release=$R2_RELEASE" \
  --label com.deepseek.role=report \
  --label "com.deepseek.report-root=$report_dir" \
  --network bridge \
  --publish "127.0.0.1:$port:8080" \
  --volume "$report_dir:/report:ro" \
  --workdir /report \
  --entrypoint python3 "$R2_IMAGE" \
  -m http.server 8080 --bind 0.0.0.0 >/dev/null
printf 'REPORT_SERVER=PASS\nurl=http://127.0.0.1:%s/%s.html\ncontainer=%s\n' \
  "$port" "$report_basename" "$REPORT_CONTAINER_NAME"
