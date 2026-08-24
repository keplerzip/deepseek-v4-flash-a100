# 目标机部署

## 前置条件

- Linux x86_64、8 张可见 A100-SXM4-80GB、可工作的 NVIDIA Container Toolkit。
- NVIDIA Driver 580.159.04，或兼容 CUDA 13.0 Update 3 的更高版本。
- 当前用户可执行 `sudo -n docker version`。
- 模型目录包含 `config.json`、tokenizer 文件和完整 safetensors 权重。

不需要网络、已有 Docker 镜像、宿主机 Python、Node、CUDA toolkit、编译器或
root shell。全部运行和测试镜像已在交付机用 4 核编译完成。

## 配置

```bash
cp r1/config/secrets.env.example r1/config/secrets.env
```

示例：

```bash
MODEL_DIR=/data/models/deepseek-v4-flash-0731
DSV4_API_KEY=replace-with-a-random-local-secret
RUNTIME_BASE=/var/tmp/dsv4-target-r1-20260820
```

`secrets.env` 被 Git 和证据收集器排除。两个方案均只把 API 发布到宿主机回环
地址和 Docker 默认 bridge 网关，不监听宿主机局域网地址。宿主机使用
`http://127.0.0.1:8005/v1`；同机 Docker 容器需要把
`host.docker.internal` 映射为 `host-gateway`，然后使用
`http://host.docker.internal:8005/v1`。API 探活地址为 `/v1/models`，根路径
`/` 返回 404 不代表服务不可达。

## 直接部署

```bash
./start_one.sh   # 方案一：GPU 0-7 / TP8 / max-num-seqs 32
# 或
./start_two.sh   # 方案二：GPU 4-7 / TP4 / max-num-seqs 16
./status_one.sh  # start_two.sh 则使用 ./status_two.sh
```

`start.sh` 先对大镜像 payload 做 SHA-256 校验，再加载三个精确 tag。若 tag
已经存在且 ID 相同则复用；若同名 tag 指向其他内容则在 `docker load` 前失败，
不会覆盖。随后 R1 对安装树、模型目录、tokenizer 和 8 张 GPU 做 preflight，
通过后才启动。方案二仍要求物理机 8 卡可见，但仅把 GPU 4–7 暴露给推理进程。
两个方案共享端口且 GPU 有重叠，不能同时运行；安全切换会停止另一方案的 owned
容器，但不删除。两者使用相同的 Docker 内部访问地址，切换方案不需要修改客户端
URL。

Docker 客户端必须使用下面的 host-gateway 映射；这不会向局域网开放端口：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

验证地址：

```text
http://host.docker.internal:8005/v1/models
```

此网络边界属于项目硬性验收条件，完整定义见 `PROJECT-SPEC.md` 和
`../manifests/deployment-contract.json`。
模型始终从
`/ai/models/deepseek-v4-flash-0731-modelscope` 只读挂载。

### 最早版本收敛

目标机若仍运行最早版，会使用以下两个服务容器名：

```text
dsv4-target-only-f8ea5bb
dsv4-dspark-f8ea5bb
```

新版在 preflight 前检查容器名及 `com.deepseek.bundle`、
`com.deepseek.mode`、`com.deepseek.vllm.commit` 三项标签。全部精确匹配才执行
最长 120 秒的优雅 stop；容器、镜像、旧项目目录和运行证据都不会删除。名称被
其他容器占用或标签异常时会 fail closed，要求人工确认，绝不按名称盲停。

完整门禁使用包内已编译的
`dsv4-a100:1281004-r1-tests-20260820` 测试镜像。它预装了已校验的 pytest、
pytest-asyncio 和 tblib，用于在目标 A100 上对已安装 R1 package 跑源码回归、
生命周期契约及 Anthropic 转换测试；目标机不会构建测试层。

## 两套 Benchmark

```bash
./benchmark_one.sh
./benchmark_two.sh
```

两套入口复用同一预编译镜像，但结果完全隔离。方案一包含并发 1–16、上下文
10K–200K（步进 10K）的 320 格矩阵；方案二包含并发 1–8 的 160 格矩阵。
两者完成后都会生成 canonical artifact 和自包含 HTML。

### 方案二容量依据

旧目标机实测模型目录为 155.44 GiB；TP8 启动时每卡模型占 19.81 GiB，KV 可用
49.83 GiB。TP4 规划按权重分片翻倍为约 39.62 GiB，并保持实测非 KV reserve
不变，估算 KV 约 30.02 GiB，对应约 11.80 个满 256K 请求。方案二按最新要求将
`max-num-seqs` 固定为 16（约 1.36 倍排队余量），性能矩阵只测 C1–8。该数值是
容量规划，不冒充目标实测；目标启动日志中的实际 KV token 数和 maximum
concurrency 才是最终权威证据。

完整 API/Claude/Codex、源码、稳定性和性能门禁：

```bash
./run-tests.sh
DSV4_SCHEME=two ./run-tests.sh
```

方案一稳定性并发 32，方案二为 16。

## 停止与回滚

```bash
./stop.sh
r1/scripts/rollback.sh
```

停止脚本会停止带本交付 ownership label 的两个方案及 rollback 容器，并调用最早版
三标签收敛检查；所有被停止的旧容器仍会保留。回滚启动精确 base tag，不会
改标记、删除或覆盖任何镜像。base preflight 会明确记录其锁定的
`len(tokenizer)=129283` / `config.vocab_size=129280` 已知旧差异；R1 正常路径仍
严格要求二者均为 `129280`。失败启动会保留容器供诊断，日志写入
`RUNTIME_ROOT/logs`。

## 报告页面

```bash
./report_one.sh
./report_two.sh
r1/scripts/stop_report.sh
DSV4_SCHEME=two r1/scripts/stop_report.sh
```

方案一页面默认监听 `127.0.0.1:8015`，方案二为 `127.0.0.1:8016`。如需从
另一台机器查看，优先 SSH 端口转发，
不要把未鉴权的报告服务直接暴露到公网。

完整门禁结束后，`serve_report.sh` 会自动优先选择
`RUNTIME_BASE/{one,two}/results/performance-{one,two}/performance-report.html`；
若真实矩阵尚未完成，则回退到交付内诚实显示 `0/320` 或 `0/160` 的初始报告。
