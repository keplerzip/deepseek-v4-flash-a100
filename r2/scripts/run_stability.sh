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

soak_dir="$RESULT_DIR/stability"
mkdir -p "$soak_dir"
docker_cmd inspect --format \
  '{"id":"{{.Id}}","restart_count":{{.RestartCount}},"started_at":"{{.State.StartedAt}}","status":"{{.State.Status}}","oom_killed":{{.State.OOMKilled}}}' \
  "$CONTAINER_NAME" >"$soak_dir/container-before.json"

monitor_name="dsv4-r2-monitor-$runtime_suffix"
if container_exists "$monitor_name"; then
  owner=$(docker_cmd inspect --format \
    '{{index .Config.Labels "com.deepseek.owner"}}' "$monitor_name" 2>/dev/null || true)
  [[ "$owner" == "$OWNER_LABEL" ]] || die "monitor name is owned by another project: $monitor_name"
  container_running "$monitor_name" && docker_cmd stop --time 10 "$monitor_name" >/dev/null
  docker_cmd rm "$monitor_name" >/dev/null
fi
docker_cmd run --detach \
  --name "$monitor_name" \
  --label "com.deepseek.owner=$OWNER_LABEL" \
  --label com.deepseek.role=monitor \
  --network none --gpus all \
  --user "$(id -u):$(id -g)" \
  --volume "$soak_dir:/results:rw" \
  --entrypoint nvidia-smi "$R2_IMAGE" \
  --query-gpu=timestamp,index,memory.used,utilization.gpu,power.draw \
  --format=csv,noheader,nounits --loop=30 --filename=/results/gpu.csv >/dev/null
cleanup_monitor() {
  if container_running "$monitor_name"; then
    docker_cmd stop --time 10 "$monitor_name" >/dev/null || true
  fi
}
trap cleanup_monitor EXIT

client_args=(
  --rm --network bridge --add-host host.docker.internal:host-gateway
  --user "$(id -u):$(id -g)"
  --volume "$R2_DIR:/r2:ro"
  --volume "$soak_dir:/results:rw"
  --entrypoint python3
)
[[ -z "${DSV4_API_KEY:-}" ]] || client_args+=(--env "DSV4_API_KEY=$DSV4_API_KEY")
set +e
docker_cmd run "${client_args[@]}" "$R2_IMAGE" \
  /r2/benchmarks/stability_soak.py --output /results/summary.json "$@"
soak_status=$?
set -e
cleanup_monitor
trap - EXIT

docker_cmd inspect --format \
  '{"id":"{{.Id}}","restart_count":{{.RestartCount}},"started_at":"{{.State.StartedAt}}","status":"{{.State.Status}}","oom_killed":{{.State.OOMKilled}}}' \
  "$CONTAINER_NAME" >"$soak_dir/container-after.json"
docker_cmd logs --timestamps "$CONTAINER_NAME" >"$soak_dir/server.log" 2>&1 || true
[[ "$(docker_cmd inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" == true ]] || die \
  'inference container is no longer running after the stability workload'
[[ "$(docker_cmd inspect --format '{{.State.OOMKilled}}' "$CONTAINER_NAME")" == false ]] || die \
  'inference container was OOM-killed during the stability workload'
[[ "$(docker_cmd inspect --format '{{.RestartCount}}' "$CONTAINER_NAME")" == 0 ]] || die \
  'inference container restarted during the stability workload'
if grep -Eiq 'CUDA illegal memory|device-side assert|EngineCore.*died|CUDA out of memory|OutOfMemoryError' \
  "$soak_dir/server.log"; then
  die "fatal server signature found; inspect $soak_dir/server.log"
fi
((soak_status == 0)) || die "stability workload failed; inspect $soak_dir"
printf 'STABILITY=PASS\nsummary=%s\ngpu_samples=%s\ncontainer_before=%s\ncontainer_after=%s\n' \
  "$soak_dir/summary.json" "$soak_dir/gpu.csv" \
  "$soak_dir/container-before.json" "$soak_dir/container-after.json"
