# 报告与数据

## 现场结论

- [A100 性能报告（Markdown）](performance-report-2026-08-13.md)
- [离线 HTML 报告](performance-report-2026-08-13.html)
- [横版汇报图](deepseek-v4-flash-a100-performance-summary-2026-08-13.png)
- [目标机现场记录](field-test-record-2026-08-13.md)
- [数据来源与证据边界](performance-source-notes-2026-08-13.md)

## 可复核材料

- `data/`：从目标机终端输出整理出的 CSV；
- `queries/`：报告计算使用的 SQL；
- `performance-report-2026-08-13.artifact.json`：报告结构化产物；
- `compatibility-report.md`：A100 实测前的历史构建机审计；
- `qa/`：报告渲染 QA 说明，失败截图不进入公开 Git。

精确性能数值多数来自操作者粘贴的终端输出，证据等级为 B；目标机原始 benchmark JSON、
GPU CSV 和完整日志尚未归档。不要把单次数据解释为 SLA。
