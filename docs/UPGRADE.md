# 从旧运行包升级到 1.2.0

本次升级不修改 Docker image 和 167GB 模型，只覆盖项目配置、脚本和文档。正在运行的
容器不会因文件覆盖自动重启；新配置在下一次启动时生效。

更新包名称：

```text
deepseek-v4-flash-a100-project-update-1.2.0-f8ea5bb-a100-field.tar.gz
deepseek-v4-flash-a100-project-update-1.2.0-f8ea5bb-a100-field.tar.gz.sha256
```

## 1. 校验更新包

```bash
cd /path/to/upload
sha256sum -c \
  deepseek-v4-flash-a100-project-update-1.2.0-f8ea5bb-a100-field.tar.gz.sha256
tar -tzf deepseek-v4-flash-a100-project-update-1.2.0-f8ea5bb-a100-field.tar.gz
```

归档内路径直接相对于项目根目录，没有 `common/image`，不会覆盖或重新导入大镜像。

## 2. 备份将被覆盖的项目文件

```bash
cd /ai/services/deepseek-v4-flash-a100/deploy

BACKUP=/ai/services/deepseek-v4-flash-a100/deploy-before-1.4.0-$(date -u +%Y%m%dT%H%M%SZ).tar.gz

tar --exclude='scripts/__pycache__' -czf "$BACKUP" \
  README.md CHANGELOG.md LICENSE THIRD_PARTY.md VERSION \
  start-production.sh stop-production.sh \
  docs config common/manifests common/licenses scripts target-only dspark \
  benchmarks/README.md reports checksums 2>/dev/null || true

echo "BACKUP=$BACKUP"
```

这个备份不包含 `common/image`、模型或 benchmark result 大目录，因此体积较小。命令中的
`|| true` 只允许旧版本缺少新文件或目录，不影响随后对备份文件的检查：

```bash
test -s "$BACKUP"
tar -tzf "$BACKUP" | head
```

## 3. 覆盖更新

```bash
cd /ai/services/deepseek-v4-flash-a100/deploy

UPDATE=/absolute/path/to/deepseek-v4-flash-a100-project-update-1.2.0-f8ea5bb-a100-field.tar.gz
tar -xzf "$UPDATE" -C .
```

更新不会删除目标机额外生成的 `benchmarks/results/`、`logs/`、`run/` 或
`reports/model-verification-*.json`。

## 4. 验证

```bash
cat VERSION
./scripts/verify_offline_bundle.sh --artifact-only
DSV4_CONFIG_ONLY=1 source ./start-production.sh
```

预期：

```text
1.2.0-f8ea5bb-a100-field
ARTIFACT_VERIFICATION=PASS
profile=256k ... gpu_memory=0.92 max_batched_tokens=4096 max_num_seqs=16 tp=8
```

如果服务当前正在运行，先让现有请求结束，再重启以应用配置：

```bash
./stop-production.sh
source ./start-production.sh
```

## 5. 回滚项目文件

```bash
cd /ai/services/deepseek-v4-flash-a100/deploy
./stop-production.sh
tar -xzf "$BACKUP" -C .
```

回滚只恢复备份中的项目文件。Docker image、模型和其他服务始终未被改动。
