# 完整离线部署

本文档描述如何把已经编译好的运行包部署到目标机。目标机不需要联网，也不需要编译
vLLM。所有命令默认从项目根目录执行。

## 1. 目标环境

- Ubuntu 22.04 x86_64；
- 8×NVIDIA A100-SXM4-80GB；
- NVIDIA Driver 580.159.04 或兼容 CUDA 13.0 Update 3 的更高版本；
- Docker 与 NVIDIA Container Toolkit；
- 操作者至少可执行 `sudo -n docker`；
- 模型已位于 `/ai/models/deepseek-v4-flash-0731-modelscope/`。

脚本不会安装或升级 NVIDIA Driver，不会停止现有服务，也不会下载模型。

## 2. 交付物

主运行包：

```text
deepseek-v4-flash-a100-runtime-f8ea5bb-ubuntu22.04-x86_64.tar.xz
deepseek-v4-flash-a100-runtime-f8ea5bb-ubuntu22.04-x86_64.tar.xz.sha256
```

当前归档大小为 13,543,585,372 bytes。解压后主要空间由 Docker archive 占用；模型
167GB 不在运行包中。

ModelScope checkpoint 缺少 DeepSeek V4 专用的 `encoding/` 和 `inference/`，现场另有：

```text
deepseek-v4-flash-0731-official-aux-7872f01.tar.xz
deepseek-v4-flash-0731-official-aux-7872f01.tar.xz.sha256
```

辅助包 SHA256 为：

```text
615a86f0414e080aeeb30fc7ed771585e60f5708fc1933d1733a18e25dfdb197
```

`target-build-seed` 是历史回退构建材料，已有 runtime 包时不需要复制或导入。

## 3. 校验并解压

```bash
cd /ai/services/deepseek-v4-flash-a100
sha256sum -c deepseek-v4-flash-a100-runtime-f8ea5bb-ubuntu22.04-x86_64.tar.xz.sha256
tar -xJf deepseek-v4-flash-a100-runtime-f8ea5bb-ubuntu22.04-x86_64.tar.xz
cd deploy
```

如果目录名不是 `deploy`，进入实际解压出的项目根目录即可。脚本按自身位置解析路径。

## 4. 只在缺失时补齐模型辅助目录

先检查：

```bash
MODEL_DIR=/ai/models/deepseek-v4-flash-0731-modelscope
test -d "$MODEL_DIR/encoding" && echo encoding=FOUND || echo encoding=MISSING
test -d "$MODEL_DIR/inference" && echo inference=FOUND || echo inference=MISSING
```

两者已经存在时不要重复解压。确实缺失时，先确认辅助包内容和校验和，再执行：

```bash
MODEL_DIR=/ai/models/deepseek-v4-flash-0731-modelscope
AUX_ARCHIVE=../deepseek-v4-flash-0731-official-aux-7872f01.tar.xz

sha256sum "$AUX_ARCHIVE"
tar -tJf "$AUX_ARCHIVE"
test ! -e "$MODEL_DIR/encoding"
test ! -e "$MODEL_DIR/inference"
tar -xJf "$AUX_ARCHIVE" -C "$MODEL_DIR"
```

该操作只增加约 30KB 辅助文件，不复制或改写 safetensors shard。

## 5. 离线包和模型预检

```bash
./scripts/verify_offline_bundle.sh --artifact-only
./scripts/target_acceptance.sh preflight
```

预期关键输出：

```text
ARTIFACT_VERIFICATION=PASS
FILE_INTEGRITY=PASS
DSPARK_WEIGHTS=PASS
INFERENCE_CORRECTNESS=NOT_TESTED
```

`INFERENCE_CORRECTNESS=NOT_TESTED` 在预检阶段是正确状态；它表示只检查了文件和
safetensors header，还没有运行模型。

## 6. 导入公共镜像

```bash
./scripts/install_offline.sh
```

脚本从 `common/image/dsv4-a100-vllm-f8ea5bb.tar` 执行 `docker load`，不访问网络。
目标镜像必须为：

```text
dsv4-a100-vllm:f8ea5bb
sha256:eb8f80df61d3124c981a1354aa0a432a3b6b7453ae588a317fa80b61e64a6159
```

`INFO native wheelhouse is not marked complete; container remains primary` 是状态说明，不是
失败。当前受支持的交付方式就是容器；原生 wheelhouse 尚未宣告完成。

## 7. 启动生产配置

```bash
source ./start-production.sh
```

脚本固定 target-only、256K、C16、4096、0.92、CUDA Graph、TP=8 和端口 8005。
默认绑定 Docker bridge 地址 `172.17.0.1`，供同机 New API 容器访问。若目标机 bridge
地址不同，可在 source 前覆盖：

```bash
DSV4_BIND_HOST=172.18.0.1 source ./start-production.sh
```

若只想检查将要使用的配置而不启动：

```bash
DSV4_CONFIG_ONLY=1 source ./start-production.sh
```

## 8. 验收服务

```bash
./target-only/status.sh
curl -fsS http://172.17.0.1:8005/v1/models | python3 -m json.tool
./target-only/smoke-test.sh
```

首次启动需要加载 48 个 shard、编译/预热并捕获 CUDA Graph。启动脚本最长等待 7200
秒；等待期间出现 `curl: (7) connection refused` 只表示 API 尚未 ready，容器退出或日志
出现致命模式才算失败。

## 9. 接入 New API

服务只绑定 Docker bridge 时，同机容器可使用：

```text
http://host.docker.internal:8005/v1
```

Linux Docker 必须给 New API 容器配置 host gateway，例如 Compose：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

如果未添加映射，也可直接使用目标机实际 bridge 地址，例如
`http://172.17.0.1:8005/v1`。不要在服务只绑定 `127.0.0.1` 时期待其他容器能够访问。

## 10. 实验性 DSpark

先停止 target-only：

```bash
./stop-production.sh
```

然后以保守配置启动：

```bash
PROFILE=256k \
EXECUTION_MODE=graph \
GPU_MEMORY_UTILIZATION=0.80 \
MAX_NUM_BATCHED_TOKENS= \
./dspark/start.sh
```

DSpark 的 `max_num_seqs=6` 来自 256K profile。不要把 0.95 或手工 16GiB KV cache
直接作为生产值；这些组合在长上下文测试中出现过 OOM 或 illegal memory access。

确认真正启用：

```bash
./dspark/status.sh
./dspark/logs.sh --tail 3000 | \
  grep -E 'SpeculativeConfig|DSpark draft model loaded|Mean acceptance|Draft acceptance'
```

## 11. 停止和回滚

```bash
./stop-production.sh
# 或实验模式
./dspark/stop.sh
```

完整卸载：

```bash
./scripts/uninstall.sh
```

停止脚本只操作具有本项目 ownership label 的精确容器名，不使用 `pkill`、`killall` 或
批量 `docker stop`。模型、日志和其他容器保持不变。
