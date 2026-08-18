# 公共离线运行时

该目录保存两种运行模式共享的构建与审计材料：

```text
licenses/       第三方许可证副本
manifests/      固定 commit、镜像 inspect、pip freeze 和构建版本
offline-build/  可提交的离线构建 Dockerfile 与内部构建脚本
image/          生成的容器镜像归档，不进入公开 Git
source/         固定 vLLM 源码 checkout/tarball，不进入公开 Git
wheelhouse/     原生 Python 回退依赖，不进入公开 Git
target-build-output/  编译产物，不进入公开 Git
```

公开仓库不分发第三方源码副本或二进制 artifacts。来源、commit 和致谢见
[`THIRD_PARTY.md`](../THIRD_PARTY.md)，大文件边界见
[项目结构](../docs/PROJECT-STRUCTURE.md)。
