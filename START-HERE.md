# DeepSeek V4 Flash A100 离线交付

> 本说明面向**完整离线包**。GitHub source-only checkout 不含预编译镜像、固定 base
> 源码压缩包或测试 wheels，不能直接执行断网部署；公开仓库只提供代码、模板、
> checksum 与 manifest。

这个交付包已经包含本机用 4 核为 A100（SM80）编译好的 vLLM 运行镜像、
R1 兼容层、离线测试镜像、测试代码和网页报告。目标机不会编译、下载或安装
任何依赖；唯一的特权接口是 `sudo -n docker`。

目标环境需保持 8 张 A100-SXM4-80GB、NVIDIA Container Toolkit，以及原机器已有的
580.159.04 驱动（或兼容 CUDA 13.0 Update 3 的更高版本）。

模型权重不在包内，继续使用目标机已有目录：

```text
/ai/models/deepseek-v4-flash-0731-modelscope
```

## 两个部署方案

| 方案 | 启动入口 | GPU / TP | 服务上限 | Benchmark |
|---|---|---:|---:|---:|
| 方案一 | `./start_one.sh` | 0–7 / TP8 | 32 | C1–16、320 格 |
| 方案二 | `./start_two.sh` | 4–7 / TP4 | 16 | C1–8、160 格 |

两套服务都固定 256K 上下文且监听同一 API 端口，因此不能同时运行。启动任一
方案会先核验 ownership，再优雅停止另一方案；容器、日志和结果均保留。`start.sh`
是 `start_one.sh` 的兼容别名。

API 不向局域网开放。宿主机使用 `http://127.0.0.1:8005/v1`，同机 Docker
容器在配置 `host.docker.internal:host-gateway` 后使用
`http://host.docker.internal:8005/v1`。两个方案的客户端 URL 相同；探活必须访问
`/v1/models`，不能用会返回 404 的根路径 `/` 判断服务状态。

## 直接启动

```bash
./start_one.sh        # 方案一：8 卡
# 或
./start_two.sh        # 方案二：4 卡
```

首次启动会校验并加载内附 Docker 镜像，然后只读挂载上述模型目录。后续启动
检测到镜像 ID 完全一致时会直接复用，不会覆盖同名但内容不同的镜像。
加载完成后，脚本还会识别最早版的 `dsv4-target-only-f8ea5bb` 和
`dsv4-dspark-f8ea5bb`。只有名称和三项 ownership 标签全部匹配时才会停止正在
运行的旧服务；旧容器、旧镜像和旧目录全部保留，不会删除。

常用命令：

```bash
./benchmark_one.sh    # 方案一 C1–16 / 320 格，并生成网页报告
./benchmark_two.sh    # 方案二 C1–8 / 160 格，并生成网页报告
./report_one.sh       # 方案一报告：http://127.0.0.1:8015
./report_two.sh       # 方案二报告：http://127.0.0.1:8016
./status_one.sh       # 查看方案一
./status_two.sh       # 查看方案二
./stop.sh             # 停止两个新版方案及标签匹配的最早版服务
./run-tests.sh        # 方案一完整验收；方案二用 DSV4_SCHEME=two ./run-tests.sh
```

如需 API key 或更换运行时目录，先复制配置模板：

```bash
cp r1/config/secrets.env.example r1/config/secrets.env
```

项目硬性约束见 `r1/docs/PROJECT-SPEC.md`；完整操作说明见 `r1/README.md` 和
`r1/docs/DEPLOYMENT.md`。

## 目录结构

```text
./                    双方案启动、benchmark、报告入口与本说明
├── r1/               运行配置、镜像、脚本、测试工具、报告和文档
├── tests/            完整门禁所需的上游源码测试（运行时只读挂载）
└── vllm/             八个经审计的 R1 overlay 源文件
```

未参与目标部署的上游开发文件不会塞在交付根目录；完整固定 base 源码快照仍在
`r1/base/`，用于审计和复现。
