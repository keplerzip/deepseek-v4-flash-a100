# 开源项目致谢与第三方声明

本项目是一套部署、验证和运维脚本，不是 DeepSeek、vLLM 或 NVIDIA 的官方发行版。
模型权重、容器镜像、Python wheels、编译产物和下列仓库的源码副本均不包含在本公开
GitHub 仓库中。它们分别受各自许可证、模型许可证和使用条款约束；本项目的许可证不会
覆盖或改变第三方项目的许可证。

## 核心项目

- [DeepSeek-V4-Flash-0731](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)：
  本项目部署的模型与 DeepSeek V4 消息编码来源。感谢 DeepSeek 团队公开模型及相关实现。
  权重也可由用户自行通过 ModelScope 获取，但本仓库不分发权重或模型辅助文件。
- [haosdent/vllm](https://github.com/haosdent/vllm)：本项目在 A100 上使用的社区 vLLM
  实现，精确固定到 commit
  `f8ea5bb163c161ef38b401d055cc5fd4a934091a`。特别感谢该社区分支提供 DeepSeek V4、
  DSpark 和 A100 fallback 路径。
- [vllm-project/vllm](https://github.com/vllm-project/vllm)：vLLM 上游项目及生态基础。
  vLLM 相关许可证副本见
  [`common/licenses/vllm-Apache-2.0.txt`](common/licenses/vllm-Apache-2.0.txt)。

## 固定的离线构建依赖

以下仓库由离线构建准备脚本按固定版本获取。公开仓库只保存下载/构建逻辑、版本清单和
许可证副本，不保存它们的 Git checkout。

| 项目 | 固定 ref / commit | 用途 |
|---|---|---|
| [NVIDIA CUTLASS](https://github.com/NVIDIA/cutlass) | `v4.4.2` / `da5e086d` | CUDA 模板与构建依赖 |
| [Triton](https://github.com/triton-lang/triton) | `v3.5.1` / `0add6826` | 离线源码依赖；最终 runtime 版本另见构建清单 |
| [DeepGEMM](https://github.com/vllm-project/DeepGEMM) | `f5a76426` | vLLM 可选内核依赖 |
| [MSA](https://github.com/vllm-project/MSA) | `890aaa1a` | vLLM 可选 attention 依赖 |
| [FlashMLA](https://github.com/vllm-project/FlashMLA) | `a8f794d1` | vLLM 可选 MLA 内核依赖 |
| [FlashKDA](https://github.com/vllm-project/FlashKDA) | `a3e42bbb` | vLLM 可选内核依赖 |
| [Qutlass](https://github.com/IST-DASLab/qutlass) | `e74319e3` | 量化 CUDA 构建依赖 |
| [tml-fa4](https://github.com/vllm-project/tml-fa4) | `b2068346` | attention 构建依赖 |
| [vLLM FlashAttention fork](https://github.com/vllm-project/flash-attention) | `28e862d2` | vLLM attention 构建依赖 |

完整 commit、submodule 版本与来源记录在
[`common/manifests/target-build-seed.txt`](common/manifests/target-build-seed.txt)，对应许可证
副本保存在 [`common/licenses/vendor/`](common/licenses/vendor/)。部分项目包含面向更新
GPU 架构的内核；A100 构建依赖社区 fork 的 SM80 fallback，并不声称所有可选内核都会
在 A100 上启用。

## 运行时生态

同时感谢 [PyTorch](https://github.com/pytorch/pytorch)、
[NVIDIA CUDA](https://developer.nvidia.com/cuda-toolkit)、
[NCCL](https://github.com/NVIDIA/nccl)、
[Hugging Face Transformers](https://github.com/huggingface/transformers)、
[safetensors](https://github.com/huggingface/safetensors) 以及所有传递依赖的维护者。
最终镜像中的精确 Python 包见
[`common/manifests/pip-freeze.txt`](common/manifests/pip-freeze.txt)，收集到的 Python
发行包许可证见
[`common/licenses/python-distribution-licenses.json`](common/licenses/python-distribution-licenses.json)。

如发现遗漏的署名或许可证信息，欢迎提交 issue 或 pull request 修正。
