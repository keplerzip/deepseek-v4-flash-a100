# target-only

当前生产推荐模式，不启用 speculative decoding，启动参数中不存在
`--speculative-config`。

| 入口 | 用途 |
|---|---|
| `start.sh` | 按当前环境变量/profile 启动 |
| `stop.sh` | 只停止本模式拥有的容器 |
| `status.sh` | 查看实际配置、API 和 GPU 状态 |
| `logs.sh` | 查看容器日志 |
| `smoke-test.sh` | API 功能验收 |
| `benchmark.sh` | 性能矩阵 |
| `stability-test.sh` | 可配置稳定性测试 |

生产环境优先使用根目录的 `source ./start-production.sh`，它会加载最终固化参数。详见
[最终定型方案](../docs/FINAL-CONFIGURATION.md)。
