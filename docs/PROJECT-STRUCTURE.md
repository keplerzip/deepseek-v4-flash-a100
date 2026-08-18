# 项目结构

公开仓库按“生产入口、运行模式、公共能力、验证证据”组织。为保持目标机已有部署命令
兼容，目录名和脚本入口不做无意义搬迁。

```text
deepseek-v4-flash-a100/
├── README.md                     # 项目首页、实测性能与快速入口
├── LICENSE                       # 本项目原创脚本/文档的 Apache-2.0
├── THIRD_PARTY.md                # 上游仓库、固定 commit 与许可证致谢
├── VERSION / CHANGELOG.md        # 项目版本和变更记录
├── BLOCKERS.md                   # 尚未完成的验收与已知风险
├── start-production.sh           # 固化 target-only 生产启动入口
├── stop-production.sh            # 精确停止本项目生产容器
├── config/
│   ├── common.env                # 两种模式共享配置
│   ├── target-only.env           # 普通推理配置
│   ├── dspark.env                # DSpark speculative 配置
│   ├── production-target.env     # A100 现场选定的生产参数
│   ├── model.env.example         # 本地模型路径模板
│   └── profiles/                 # 32K / 128K / 256K / 1M 上下文档位
├── target-only/                  # target-only 启停、状态、日志和测试入口
├── dspark/                       # DSpark 启停、状态、日志和测试入口
├── scripts/
│   ├── mode_action.sh / lib.sh   # 两种模式共用的核心运行逻辑
│   ├── verify_*.sh / *.py        # 环境、模型、镜像和离线包验证
│   ├── prepare_*.sh              # 联网构建机准备离线依赖
│   ├── package_*.sh              # 离线 artifact 打包
│   ├── benchmark_api.py          # OpenAI API benchmark 客户端
│   └── collect_metrics.py        # GPU 指标采集
├── common/
│   ├── manifests/                # 固定版本、镜像 inspect 与 pip freeze
│   ├── licenses/                 # 第三方许可证归档
│   └── offline-build/            # 可提交的离线构建 Dockerfile/脚本
├── benchmarks/
│   ├── prompts/                  # 可复现实验输入配方
│   └── README.md                 # benchmark 使用说明
├── reports/
│   ├── performance-report-*.md   # 脱敏后的现场性能报告
│   ├── data/                     # 报告使用的结构化摘录
│   └── queries/                  # 报告指标查询
├── docs/                         # 部署、运维、测试、排障和架构文档
└── checksums/README.md            # 完整离线包 checksum 的生成说明
```

## 不进入公开 Git 的目录

以下路径保留为运行或构建约定，但内容由脚本生成，或由离线 artifact bundle 单独提供：

```text
common/image/                     # docker save 导出的镜像
common/wheelhouse/                # 原生 Python 回退依赖
common/source/                    # 固定 commit 的源码 checkout/tarball
common/target-build-output/       # 目标架构编译产物
common/offline-build/vendor-src/  # 固定第三方源码 checkout
common/offline-build/vendor-export/
common/offline-build/artifacts/
benchmarks/results/               # 每次 benchmark 原始 JSON/CSV
logs/                             # 服务与构建运行日志
run/                              # PID、锁与运行状态
```

本机模型路径写入 `config/model.env`，该文件不会进入 Git；公开仓库只提供
`config/model.env.example`。完整离线包生成后，`checksums/SHA256SUMS` 也在包内生成，
而不是提交一个引用缺失大文件的 checksum 清单。

## 代码边界

- `start-production.sh` 和 `target-only/*` 是当前推荐生产路径。
- `dspark/*` 是实验路径，和 target-only 共享 GPU、端口与公共锁，不能同时启动。
- `scripts/mode_action.sh` 是容器启动参数的唯一公共实现，模式入口只负责选择模式。
- `common/manifests/target-build-seed.txt` 与 `THIRD_PARTY.md` 是依赖来源的审计入口。
- `reports/` 只保留适合公开的整理后证据；主机原始审计、Token、用户配置和构建日志均
  被 `.gitignore` 排除。
