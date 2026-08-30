# R2.1 部署说明

## 前置条件

- Ubuntu 22.04；
- 8 张 A100-SXM4-80GB，全部对容器可见；
- NVIDIA Driver 不低于 580.126.20，并已安装 NVIDIA Container Toolkit；
- `sudo -n docker version` 成功；
- 模型目录 `/ai/models/deepseek-v4-flash-0731-modelscope` 存在。

## 安装和启动

解压完整离线包后：

```bash
sha256sum -c ../deepseek-v4-flash-a100-r2.1-offline-20260830.tar.gz.sha256
./start_one.sh
```

若目标机已通过旧完整包加载镜像
`dsv4-a100:20260826-r2-sm80@sha256:5d420df326cf...`，使用增量交付目录：

```bash
./update-from-r2.sh one
```

命令会精确校验旧镜像 ID，在 Docker `--network none` 下创建 R2.1 覆盖层并启动
target。它不运行 pip、不编译 CUDA；旧镜像不匹配时会停止，不能降级为模糊修补。

启动器按顺序校验镜像 tar、镜像 ID、source revision、GPU、Driver、模型结构、
DSpark/MTP 权重、四名称限制和端口，然后启动 target。切换方案：

```bash
./start_two.sh
```

启动方案二会停止标签匹配的 R2 target，反向亦然。停止的旧容器会在下一次同方案启动
时删除并重建；模型、镜像、宿主结果目录不会删除。

## 配置

只在需要时创建：

```bash
cp r2/config/secrets.env.example r2/config/secrets.env
```

可配置模型路径、API key、运行根目录、显存利用率和 cache profile。GPU、TP、C16、
物理 1M 上限、四个精确 alias、网络发布边界和方案 speculative method 由
`config/release.env` 最后加载并设为只读，旧 `secrets.env` 不能覆盖。

临时切换 cache profile：

```bash
PREFIX_CACHE_PROFILE=legacy ./start_one.sh
PREFIX_CACHE_PROFILE=32768 ./start_one.sh
```

临时启动 DSpark 测试 k：

```bash
DSV4_DSPARK_K=5 ./start_two.sh
```

日常生产候选仍是 k=7。k=1/3/5 只用于筛选，不会改写配置文件。

## Docker 内部访问

客户端容器示例：

```bash
sudo -n docker run --rm \
  --add-host host.docker.internal:host-gateway \
  curlimages/curl:latest \
  http://host.docker.internal:8005/v1/models
```

服务容器的 HostConfig 应同时出现 `127.0.0.1:8005` 与 bridge gateway；若出现
`0.0.0.0:8005`，立即停止并视为网络门禁失败。

## 运维

```bash
./status_one.sh
./status_two.sh
DSV4_SCHEME=target r2/scripts/logs.sh --tail 300
DSV4_SCHEME=dspark r2/scripts/logs.sh --tail 300
./stop.sh
```

报告服务器是幂等的：重复执行 `report_one.sh`/`report_two.sh` 会返回现有 URL。
