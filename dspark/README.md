# DSpark（实验模式）

该模式使用同一份 DeepSeek-V4-Flash-0731 权重中的 `mtp.*` 模块，不需要第二个 draft
model。固定方法为 `dspark`、5 speculative tokens、greedy。

入口与 target-only 相同：`start.sh`、`stop.sh`、`status.sh`、`logs.sh`、
`smoke-test.sh`、`benchmark.sh` 和 `stability-test.sh`。

DSpark 与 target-only 使用同一组 8 张 GPU、同一端口和公共锁，不能同时运行。现场出现过
0.95 显存下 OOM 和另一长上下文 CUDA Graph 组合的 illegal memory access，因此当前不
作为生产定型方案。详见 [故障排查](../docs/TROUBLESHOOTING.md)和
[已知限制](../docs/KNOWN-LIMITATIONS.md)。
