# DeepSeek V4 Flash A100 目标交付规范

本文是目标交付的项目级硬约束。实现、启动脚本、验收测试和后续压缩包不得弱化
这些要求。机器可读版本位于 `../manifests/deployment-contract.json`。

## 运行环境

- 目标机只要求 8 张 A100-SXM4-80GB、已有模型目录和 NVIDIA Container Toolkit。
- 模型目录固定为 `/ai/models/deepseek-v4-flash-0731-modelscope`，只读挂载。
- 唯一特权接口是 `sudo -n docker`；目标机不得编译、联网下载或安装依赖。
- 服务最大上下文固定为 262,144 tokens，不启用推测解码。

## 部署方案

- 方案一使用 GPU 0–7、TP8、`max-num-seqs=32`。
- 方案二使用 GPU 4–7、TP4、`max-num-seqs=16`，并固定
  `VLLM_SPARSE_DENSE_QUERY_BLOCK=4` 以满足 A100 shared-memory 上限。
- 两个方案使用相同端口且 GPU 4–7 重叠，任何时刻只能运行一个推理容器。
- 切换方案必须停止另一方案的项目自有容器，但保留容器、日志和测试结果。

## API 网络边界

- API 只允许宿主机本地进程和同机 Docker 容器访问，不允许向局域网发布。
- 推理容器内部监听 `0.0.0.0:8005`，但宿主机只发布
  `127.0.0.1:8005` 和运行时发现的 Docker 默认 bridge 网关 `:8005`。
- 启动命令不得使用 `--network host`，也不得发布 `0.0.0.0:8005`。
- 宿主机客户端使用 `http://127.0.0.1:8005/v1`。
- Docker 客户端必须配置
  `host.docker.internal:host-gateway`，并使用
  `http://host.docker.internal:8005/v1`。两个方案的 URL 完全相同。
- 可用性检查固定为 `GET /v1/models`。根路径 `/` 可以返回 404，不能据此判定
  服务不可用。

## 验收与报告

- 方案一性能矩阵为 C1–16，方案二为 C1–8；上下文均为 10K–200K、步进 10K。
- 每格必须校准实际 prompt tokens、隔离前缀缓存，并原子保存可续跑 CSV。
- 报告与推理 API 是不同网络边界；报告只有显式设置 `REPORT_HOST=0.0.0.0`
  时才允许局域网访问。
- 项目契约测试必须验证 Docker-only API 发布、方案互斥、端口和探活路径。

## 原始归档热修复

- 已部署的 `2026.08.20-r1` offline 双方案归档使用根目录
  `hotfix_archive_20260821.sh` 收敛到 `2026.08.21-hf1`。
- 脚本必须先备份再修改且可重复执行；仅在显式传入 `one` 或 `two` 时停止并
  重启对应方案，不带参数时不得触碰运行中的服务。
