#!/usr/bin/env bash
set -euo pipefail

hotfix_id=2026.08.21-hf1
restart_scheme=${1:-}

die() {
  printf '[dsv4-hotfix] ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[dsv4-hotfix] %s\n' "$*"
}

case "$restart_scheme" in
  "" | one | two) ;;
  -h | --help)
    printf 'usage: %s [one|two]\n' "${0##*/}"
    printf 'no argument patches only; one/two patches and restarts that scheme\n'
    exit 0
    ;;
  *) die "argument must be one, two, or empty" ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "$script_dir/r1/VERSION" ]]; then
  root_dir=$script_dir
elif [[ -f "$PWD/r1/VERSION" ]]; then
  root_dir=$(pwd -P)
else
  die "put this script in the extracted offline package root and run it there"
fi

cd "$root_dir"
[[ "$(<r1/VERSION)" == 2026.08.20-r1 ]] || die \
  "unsupported package version: $(<r1/VERSION)"
for required in \
  start_one.sh start_two.sh benchmark_one.sh benchmark_two.sh \
  r1/benchmarks/performance_matrix.py \
  r1/config/schemes/two.env r1/config/target.env \
  r1/scripts/lib.sh r1/scripts/preflight.sh r1/scripts/start.sh \
  r1/scripts/status.sh; do
  [[ -f "$required" ]] || die "not the dual-scheme offline package; missing $required"
done

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="r1/hotfix-backups/$timestamp"
backup_suffix=0
while [[ -e "$backup_dir" ]]; do
  backup_suffix=$((backup_suffix + 1))
  backup_dir="r1/hotfix-backups/$timestamp-$backup_suffix"
done
mkdir -p "$backup_dir"
backup_files=(
  r1/benchmarks/performance_matrix.py
  r1/config/schemes/two.env
  r1/config/target.env
  r1/scripts/lib.sh
  r1/scripts/preflight.sh
  r1/scripts/start.sh
  r1/scripts/status.sh
  r1/docs/PROJECT-SPEC.md
  r1/manifests/deployment-contract.json
)
for path in "${backup_files[@]}"; do
  [[ -e "$path" ]] || continue
  [[ ! -L "$path" ]] || die "refusing to replace symlink: $path"
  mkdir -p "$backup_dir/$(dirname -- "$path")"
  cp -p -- "$path" "$backup_dir/$path"
done

benchmark=r1/benchmarks/performance_matrix.py
sed -i \
  -e 's/def normalize_base_url(value: str) -> str:/def normalize_origin(value: str) -> str:/' \
  -e 's@return base if base.endswith("/v1") else base + "/v1"@return base.removesuffix("/v1")@' \
  -e 's/self.base_url = normalize_base_url(base_url)/self.origin = normalize_origin(base_url)/' \
  -e 's/self.base_url + path/self.origin + path/' \
  -e 's@client.request("/chat/completions"@client.request("/v1/chat/completions"@' \
  -e 's@client.json("/models")@client.json("/v1/models")@' \
  "$benchmark"
# A previously issued field hotfix changed only this URL expression. Replace
# the request URL structurally so both the archive original and that partial
# state converge to the same implementation.
sed -i \
  '/request = urllib.request.Request(/,+1 s@^[[:space:]]*.*,$@            self.origin + path,@' \
  "$benchmark"

scheme_two=r1/config/schemes/two.env
if ! grep -Fxq 'VLLM_SPARSE_DENSE_QUERY_BLOCK=4' "$scheme_two"; then
  sed -i '/^TENSOR_PARALLEL_SIZE=4$/a VLLM_SPARSE_DENSE_QUERY_BLOCK=4' \
    "$scheme_two"
fi

target_env=r1/config/target.env
sed -i \
  -e '/^HOST=/c\HOST=${HOST:-0.0.0.0}' \
  -e '/^API_PROBE_HOST=/d' \
  -e '/^HOST_PUBLISH_ADDRESS=/d' \
  -e '/^NETWORK_MODE=/c\NETWORK_MODE=bridge' \
  "$target_env"
sed -i '/^PORT=/a\API_PROBE_HOST=127.0.0.1\
HOST_PUBLISH_ADDRESS=127.0.0.1' "$target_env"

library=r1/scripts/lib.sh
sed -i \
  's/printf '\''http:\/\/%s:%s%s'\'' "$HOST" "$PORT" "$1"/printf '\''http:\/\/%s:%s%s'\'' "$API_PROBE_HOST" "$PORT" "$1"/' \
  "$library"

preflight=r1/scripts/preflight.sh
sed -i \
  '/only NETWORK_MODE=/c\[[ "$NETWORK_MODE" == bridge ]] || die "only NETWORK_MODE=bridge is supported"' \
  "$preflight"
if ! grep -Fq 'container bind host must remain 0.0.0.0' "$preflight"; then
  sed -i '/only NETWORK_MODE=bridge is supported/a\
[[ "$HOST" == 0.0.0.0 ]] || die "container bind host must remain 0.0.0.0"\
[[ "$API_PROBE_HOST" == 127.0.0.1 ]] || die "host API probe must remain on loopback"\
[[ "$HOST_PUBLISH_ADDRESS" == 127.0.0.1 ]] || die "host API publication must remain on loopback"\
[[ "$PORT" == 8005 ]] || die "API port must remain 8005"' "$preflight"
fi

start_script=r1/scripts/start.sh
if ! grep -Fq 'docker_bridge_gateway=$(docker_cmd network inspect' \
  "$start_script"; then
  sed -i '/^if container_exists "$launch_container"; then$/i\
docker_bridge_gateway=$(docker_cmd network inspect "$NETWORK_MODE" --format '\''{{(index .IPAM.Config 0).Gateway}}'\'')\
[[ "$docker_bridge_gateway" =~ ^([0-9]{1,3}[.]){3}[0-9]{1,3}$ ]] || die "could not resolve the Docker bridge gateway: $docker_bridge_gateway"' \
    "$start_script"
fi
if ! grep -Fq -- '--publish "$docker_bridge_gateway:$PORT:$PORT"' \
  "$start_script"; then
  sed -i '/^  --network /c\
  --network "$NETWORK_MODE" \\\
  --publish "$HOST_PUBLISH_ADDRESS:$PORT:$PORT" \\\
  --publish "$docker_bridge_gateway:$PORT:$PORT" \\' "$start_script"
fi
if ! grep -Fq 'VLLM_SPARSE_DENSE_QUERY_BLOCK=${VLLM_SPARSE_DENSE_QUERY_BLOCK:-8}' \
  "$start_script"; then
  sed -i '/--env "TOKENIZERS_PARALLELISM=$TOKENIZERS_PARALLELISM" \\/a\
  --env "VLLM_SPARSE_DENSE_QUERY_BLOCK=${VLLM_SPARSE_DENSE_QUERY_BLOCK:-8}" \\' \
    "$start_script"
fi
if ! grep -Fq 'network={{.HostConfig.NetworkMode}}' "$start_script"; then
  sed -i "/docker_cmd inspect --format 'status={{.State.Status}}'/a\\
  docker_cmd inspect --format 'network={{.HostConfig.NetworkMode}}' \"\$launch_container\"\\
  docker_cmd inspect --format 'ports={{json .HostConfig.PortBindings}}' \"\$launch_container\"" \
    "$start_script"
fi

status_script=r1/scripts/status.sh
sed -i \
  -e 's/api=ready url=/api=ready host_url=/' \
  -e 's/api=not-ready url=/api=not-ready host_url=/' \
  "$status_script"
if ! grep -Fq 'docker_url=http://host.docker.internal:' "$status_script"; then
  sed -i '/api=ready host_url=/a\
  printf '\''docker_url=http://host.docker.internal:%s/v1\\n'\'' "$PORT"\
  for container in "$CONTAINER_NAME" "${CONTAINER_NAME}-base-rollback"; do\
    container_running "$container" || continue\
    docker_cmd port "$container" "$PORT/tcp"\
  done' "$status_script"
fi

mkdir -p r1/docs r1/manifests
sed -n 's/^SPEC|//p' >r1/docs/PROJECT-SPEC.md <<'SPEC_EOF'
SPEC|# DeepSeek V4 Flash A100 目标交付规范
SPEC|
SPEC|- 目标机只依赖已有模型、离线镜像和 `sudo -n docker`，不得现场编译或联网安装。
SPEC|- 方案一使用 GPU 0–7、TP8、max sequences 32；方案二使用 GPU 4–7、TP4、max sequences 16。
SPEC|- 方案二固定 `VLLM_SPARSE_DENSE_QUERY_BLOCK=4`，规避 A100 shared-memory 超限。
SPEC|- 两个方案共享 8005 端口且 GPU 重叠，任一时刻只能运行一个方案。
SPEC|- API 仅允许宿主机回环和同机 Docker 容器访问，不得向局域网发布。
SPEC|- 宿主机地址为 `http://127.0.0.1:8005/v1`。
SPEC|- Docker 客户端配置 `host.docker.internal:host-gateway`，地址为
SPEC|  `http://host.docker.internal:8005/v1`。
SPEC|- 探活路径为 `/v1/models`；根路径 `/` 返回 404 不代表服务失败。
SPEC|- 报告页面是独立边界，只有显式设置 `REPORT_HOST=0.0.0.0` 才向局域网开放。
SPEC|- 性能矩阵：方案一 C1–16，方案二 C1–8；上下文 10K–200K，步进 10K。
SPEC|- 原始 offline 包应用的补丁版本记录为 `2026.08.21-hf1`。
SPEC_EOF

sed -n 's/^JSON|//p' >r1/manifests/deployment-contract.json <<'JSON_EOF'
JSON|{
JSON|  "schema_version": 1,
JSON|  "hotfix": "2026.08.21-hf1",
JSON|  "api_network": {
JSON|    "scope": "host-and-docker-internal",
JSON|    "host_base_url": "http://127.0.0.1:8005/v1",
JSON|    "docker_base_url": "http://host.docker.internal:8005/v1",
JSON|    "docker_host_mapping": "host.docker.internal:host-gateway",
JSON|    "health_path": "/v1/models",
JSON|    "lan_exposed": false,
JSON|    "host_network_forbidden": true
JSON|  },
JSON|  "scheme_exclusion": {"simultaneous_run_allowed": false}
JSON|}
JSON_EOF

must_contain() {
  local path=$1
  local text=$2
  grep -Fq -- "$text" "$path" || die "validation failed: $path lacks $text"
}

must_contain "$benchmark" 'def normalize_origin(value: str) -> str:'
must_contain "$benchmark" 'self.origin + path'
must_contain "$benchmark" '"/v1/chat/completions"'
must_contain "$benchmark" 'client.json("/v1/models")'
must_contain "$scheme_two" 'VLLM_SPARSE_DENSE_QUERY_BLOCK=4'
must_contain "$target_env" 'HOST=${HOST:-0.0.0.0}'
must_contain "$target_env" 'API_PROBE_HOST=127.0.0.1'
must_contain "$target_env" 'HOST_PUBLISH_ADDRESS=127.0.0.1'
must_contain "$target_env" 'NETWORK_MODE=bridge'
must_contain "$library" '"$API_PROBE_HOST" "$PORT" "$1"'
must_contain "$start_script" '--network "$NETWORK_MODE"'
must_contain "$start_script" '--publish "$HOST_PUBLISH_ADDRESS:$PORT:$PORT"'
must_contain "$start_script" '--publish "$docker_bridge_gateway:$PORT:$PORT"'
if grep -Fq -- '--network host' "$start_script"; then
  die "validation failed: inference start still uses --network host"
fi
for script in "$library" "$preflight" "$start_script" "$status_script"; do
  bash -n "$script" || die "bash syntax validation failed: $script"
done

{
  printf 'HOTFIX_ID=%s\n' "$hotfix_id"
  printf 'APPLIED_AT_UTC=%s\n' "$timestamp"
  printf 'SOURCE_VERSION=%s\n' "$(<r1/VERSION)"
  printf 'BACKUP_DIR=%s\n' "$backup_dir"
} >r1/manifests/post-archive-hotfix.env

log "HOTFIX_APPLY=PASS id=$hotfix_id backup=$root_dir/$backup_dir"
log "API boundary: host loopback + same-host Docker only; LAN publication is disabled"

if [[ -n "$restart_scheme" ]]; then
  log "stopping owned inference containers before restarting scheme $restart_scheme"
  "$root_dir/stop.sh"
  log "starting scheme $restart_scheme"
  "$root_dir/start_${restart_scheme}.sh"
else
  log "files patched; service was not touched"
  log "apply by running: $root_dir/stop.sh && $root_dir/start_one.sh"
  log "or:                $root_dir/stop.sh && $root_dir/start_two.sh"
fi
