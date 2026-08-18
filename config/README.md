# 配置目录

| 文件 | 用途 |
|---|---|
| `model.env.example` | 本地模型绝对路径模板；复制为不入 Git 的 `model.env` |
| `common.env` | 镜像、端口、GPU、TP、离线环境和容器公共默认值 |
| `production-target.env` | 最终 target-only 生产参数 |
| `target-only.env` | target-only 模式身份和字段实测默认值 |
| `dspark.env` | 实验性 DSpark 参数 |
| `profiles/*.env` | 32K、128K、256K、1M 上下文与模式并发上限 |

生产环境应通过 `source ./start-production.sh` 加载配置，不要单独 source 多个模式文件。
最终值和覆盖顺序见 [最终定型方案](../docs/FINAL-CONFIGURATION.md)。
