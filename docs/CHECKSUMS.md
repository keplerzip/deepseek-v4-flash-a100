# 离线包校验清单

`SHA256SUMS` 和 `MODEL_SHA256SUMS` 是完整离线包的生成文件。公开 source-only 仓库
不提交它们，因为对应的镜像、wheels、源码快照和模型权重并不通过 GitHub 分发。

准备好全部离线 artifacts 后，生成 bundle 清单：

```bash
./scripts/update_checksums.sh
```

模型权重的可选清单单独生成：

```bash
./scripts/generate_model_sha256.sh
```
