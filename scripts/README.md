# 脚本索引

脚本保持在单层目录，是为了兼容已经部署到目标机的命令。按用途查找如下。

## 生产运行

- `mode_action.sh`、`lib.sh`：两种模式的公共核心，不建议直接调用；
- `gpu_guard.sh`：GPU、容器、端口和公共锁保护；
- `select_profile.sh`：切换上下文 profile；
- `install_offline.sh`、`uninstall.sh`：导入和精确回滚本项目运行时。

## 验证与验收

- `inspect_environment.sh`：只读环境审计；
- `verify_model.py`：48 shards、index、metadata 和 DSpark 权重验证；
- `verify_image.sh`、`verify_offline_bundle.sh`、`verify_target_build_seed.sh`；
- `target_acceptance.sh`、`offline_inference_test.sh`、`restart_recovery_test.sh`。

## 构建与打包

- `prepare_online_bundle.sh`、`prepare_target_build_seed.sh`、`prepare_wheelhouse.sh`；
- `build_on_target_offline.sh`；
- `package_bundle.sh`、`package_compiled_runtime.sh`、`package_target_build_seed.sh`；
- `update_checksums.sh`、`generate_model_sha256.sh`。

## 测试与指标

- `api_smoke_test.py`、`benchmark_api.py`、`stability_test.py`；
- `run_benchmark.sh`、`run_dspark_token_matrix.sh`、`run_stability.sh`；
- `collect_metrics.py`、`finalize_benchmark.py`、`compare_results.py`。

## 客户端

- `configure_codex_client.sh`：生成独立 Codex CLI profile；
- `retire_claude_client.sh`：移出 Claude 活动配置但保留历史，可恢复。

用户优先从根目录 `start-production.sh`，或 `target-only/`、`dspark/` 的薄入口开始；不要
绕过模式入口直接拼接 vLLM 参数。
