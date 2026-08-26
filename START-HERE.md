# DeepSeek V4 Flash A100 R2 离线交付

这是面向 Ubuntu 22.04、8×A100-SXM4-80GB 目标机的完整离线包。目标机不编译、
不下载依赖；所有 vLLM、Python、CUDA 用户态库和 SM80 内核都在内附 Docker 镜像中。
目标用户只需能运行 `sudo -n docker`。

模型权重继续使用：

```text
/ai/models/deepseek-v4-flash-0731-modelscope
```

## 启动

```bash
./start_one.sh    # target，8 卡 TP8，max-num-seqs=16
# 或
./start_two.sh    # DSpark greedy k=7，8 卡 TP8，max-num-seqs=16
```

两方案互斥，启动器只会停止 ownership label 匹配的旧服务，不会删除模型、旧镜像或
运行证据。首次启动校验并加载 `r2/images/dsv4-a100-r2-image.tar`；同名镜像 ID 不同
时会拒绝覆盖。

服务公开四个名称：

```text
deepseek-v4-flash                  256K API 限制
deepseek-v4-flash[1M]              1M API 限制
deepseek-v4-flash-claude           256K API 限制
deepseek-v4-flash-claude[1M]       1M API 限制
```

它们共享一个模型和 KV 池。Claude 名称可用于 `/v1/messages`，普通名称可用于
OpenAI-compatible `/v1/chat/completions`。

## 网络边界

- 宿主机：`http://127.0.0.1:8005/v1`
- 同机 Docker：给客户端加入 `--add-host host.docker.internal:host-gateway`，访问
  `http://host.docker.internal:8005/v1`
- 局域网其他机器：默认不可见

不要把宿主发布地址改成 `0.0.0.0`。容器内的 `0.0.0.0` 只是为了同时接受宿主回环
和 bridge gateway 两条受控路径。

## 验收和性能测试

```bash
./run-tests.sh
./benchmark_cache_profiles.sh
./benchmark_dspark_k.sh
./benchmark_one.sh
./benchmark_two.sh
DSV4_SCHEME=target r2/scripts/run_stability.sh --duration-hours 24
DSV4_SCHEME=dspark r2/scripts/run_stability.sh --duration-hours 24
```

完整矩阵每方案 60 格，每格 C16，总计 960 个长请求；输入为
200K/400K/600K/800K/1000K，输出为 10K/20K/30K，缓存命中目标为
80%/85%/90%/95%。CSV 每格原子保存，可中断续跑。

报告：

```bash
./report_one.sh    # http://127.0.0.1:8015/long-context-matrix.html
./report_two.sh    # http://127.0.0.1:8016/long-context-matrix.html
```

报告容器已运行时命令会直接返回现有 URL，不再报“already running”。

如需 API key：

```bash
cp r2/config/secrets.env.example r2/config/secrets.env
# 编辑 DSV4_API_KEY，且不要把该文件上传到 Git
```

完整说明见 `r2/README.md`、`r2/docs/DEPLOYMENT.md` 和 `r2/docs/TESTING.md`。
