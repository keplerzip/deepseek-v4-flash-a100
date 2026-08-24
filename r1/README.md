# DeepSeek V4 Flash A100 Target R1

> GitHub 仓库是 source-only 视图，不含 `r1/images/` 中的预编译镜像、固定 base
> 源码压缩包或 test wheelhouse。下文关于“已内附”的描述针对完整离线交付包。

这是完全独立的新交付；不会修改旧项目目录、旧镜像、旧容器定义或旧运行证据。
启动和停止入口只会在名称及三项 ownership 标签全部匹配时，优雅停止最早版
仍在运行的服务容器，并保留容器本身。两个方案都固定 `262144` token 上下文且
不启用 speculative/DSpark/MTP：方案一使用 GPU 0–7、TP8、`max-num-seqs=32`；
方案二使用 GPU 4–7、TP4、`max-num-seqs=16`。
离线版已经在交付机用 4 核为 A100 SM80 编译完全部 CUDA/vLLM 产物，并把
固定 base、R1 运行镜像和源码测试镜像去重封装。目标机不会编译或联网。

## 权限模型

目标机唯一需要的特权命令是 `sudo -n docker ...`。所有脚本会先尝试当前用户
可用的 Docker；不可用时自动切换为 `sudo -n docker`。目标机部署、测试、性能
矩阵和 artifact 生成均不要求宿主机 Python、Node、npm 或额外 sudo。

目标机模型路径默认固定为：

```text
/ai/models/deepseek-v4-flash-0731-modelscope
```

解压后在交付根目录选择方案启动：

```bash
./start_one.sh
./start_two.sh
```

它会先校验 `r1/images/dsv4-a100-r1-images.tar`，通过 `sudo -n docker`
加载精确镜像，再执行模型、GPU、tokenizer 和安装树 preflight 后启动服务。
同名镜像若 ID 不同会拒绝覆盖；完全一致则直接复用。
随后启动器会收敛最早 `f8ea5bb` 版本的 target-only/DSpark 服务容器：必须同时
匹配固定名称、bundle、mode 和 commit 标签才允许停止，并且只 stop、不 remove。

仅在需要 API key 或覆盖默认值时创建未跟踪的配置：

```bash
cp r1/config/secrets.env.example r1/config/secrets.env
```

可选设置 `DSV4_API_KEY`、`RUNTIME_BASE`、显存比例。

两方案共享端口且重叠使用 GPU 4–7，启动器会在 ownership 校验通过后自动停止
另一方案，但不删除其容器或证据。运行时目录分别为
`/var/tmp/dsv4-target-r1-20260820/one` 和 `.../two`。

## Benchmark 与完整门禁

直接执行对应的可续跑性能矩阵并生成自包含网页：

```bash
./benchmark_one.sh
./benchmark_two.sh
```

方案一为 10K–200K（步进 10K）× C1–16，共 320 格；方案二上下文轴相同，
并发按卡数缩为 C1–8，共 160 格。两者的 CSV、artifact 和 HTML 互不覆盖。

完整门禁默认为方案一；方案二显式选择环境变量：

```bash
./run-tests.sh
DSV4_SCHEME=two ./run-tests.sh
```

它依次校验/加载固定离线镜像、跑静态包测试、启动服务、跑 API 验收、
完整 target-only 工具矩阵、500 请求稳定性门禁、对应方案性能矩阵、
生成 canonical report artifact 和可直接查看的真实 HTML，最后打包无密钥证据。
性能 CSV 会逐格原子保存；中断后重跑同一命令会续跑。
任一门禁失败时，顶层脚本仍会尽力生成 partial report 并收集当时的无密钥证据，
然后保留非零退出码。

分步执行和故障处理见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 与
[docs/TESTING.md](docs/TESTING.md)。兼容边界见
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。

## 报告

交付内的两个 HTML 都是自包含网页：方案一诚实显示 `0/320`，方案二显示
`0/160`（本构建机无 A100）；所有未测性能字段为 null。真实完成态会增加
P95 TTFT 和总吞吐热力图。可用 Docker 本地查看：

```bash
./report_one.sh
./report_two.sh
```

目标跑完后，Docker 内会在各自性能结果目录同时生成新的 canonical artifact 与
`performance-report.html`。对应 `report_*.sh` 会优先展示真实报告。将 CSV 带回
有 Data Analytics builder 的工作机可再做正式 Chromium 验证发布：

```bash
DSV4_SCHEME=one r1/reports/build_report.sh CSV路径
DSV4_SCHEME=two r1/reports/build_report.sh CSV路径
```

目标机查看结果不需要 Node。

## 目录结构

```text
r1/
├── base/          固定 1281004 source snapshot 与校验值（审计/复现）
├── config/        公共 target 配置、one/two 方案与未跟踪密钥模板
├── docker/        不联网的八文件 R1 overlay 与离线 source-test 层
├── images/        最终包内的预编译去重镜像、校验和和精确 ID 清单
├── manifests/     base/backport/parser/benchmark/验证范围锁
├── scripts/       构建、启动、验收、测试、回滚和打包入口
├── tests/         API、工具矩阵、进程连续性与包契约
├── test-wheelhouse/ 固定 pytest/pytest-asyncio/tblib wheels，仅供测试层
├── benchmarks/    双方案稳定性和 320/160 格性能矩阵
├── reports/       canonical artifact、可移植 HTML 与目标侧封装器
└── docs/          部署、测试和兼容边界
```

交付根目录另含双方案日常入口、完整门禁所需的只读 `tests/`，以及八个被 overlay
manifest 锁定的 `vllm/` 源文件；其余上游开发目录不会进入最终离线包。
