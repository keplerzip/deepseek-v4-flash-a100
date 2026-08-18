# 文档中心

## 首次阅读

1. [最终定型方案](FINAL-CONFIGURATION.md)：生产配置、参数来源和验收边界；
2. [完整离线部署](DEPLOYMENT.md)：从模型预检到服务启动；
3. [生产运维](OPERATIONS.md)：状态、日志、GPU guard、性能抽查和停止；
4. [已知限制](KNOWN-LIMITATIONS.md)：尚未完成的稳定性验证和实验模式风险。

## 使用指南

- [Codex CLI 接入](CODEX-CLI.md)
- [测试与 benchmark](TESTING.md)
- [故障排查](TROUBLESHOOTING.md)
- [旧部署升级](UPGRADE.md)
- [离线包校验清单](CHECKSUMS.md)

## 设计与审计

- [系统架构](ARCHITECTURE.md)
- [项目结构](PROJECT-STRUCTURE.md)
- [开源依赖与致谢](../THIRD_PARTY.md)
- [A100 现场性能报告](../reports/performance-report-2026-08-13.md)
- [报告与数据索引](../reports/README.md)

`config/production-target.env` 是生产可变参数的机器可读来源，
`start-production.sh` 是唯一推荐的一键生产入口。文档中的历史测试组合不能覆盖这两个
文件的最终值。
