# Endstone Status API

[English](README.md) · [游戏内 HUD](https://github.com/ReallocAll/endstone-show-status)

面向 Minecraft Bedrock Dedicated Server 的 Endstone 公开只读 HTTP JSON API。**无认证、不附带网页、不依赖游戏内 HUD**。允许纯静态官网、机器人、移动端及监控系统直接通过统一的 v1 JSON 契约访问。管理员通过 TOML 控制公开范围。

## 安装

要求 Python 3.10+、Endstone 0.11+；安装 wheel 会同时安装 `psutil>=5` 依赖。在 Endstone 使用的 Python 环境执行：

```bash
python -m pip install ./endstone_status_api-0.1.0-py3-none-any.whl
```

安装后重启 Endstone。默认仅监听 `127.0.0.1:19134`，建议通过 HTTPS 反向代理或 Tunnel 对外公开。如需直接监听公网，将 `[http].host` 修改为 `0.0.0.0`。不可与旧版 show-status HTTP 服务同时占用相同端口。

## 配置

首次启动生成 `plugins/status_api/config.toml`，完整配置见 [英文 README](README.md)。核心开关如下：

```toml
[endpoints]
status = true
players = false
history = true
chat = false

[chat]
max_messages = 100
```

关闭接口后返回 HTTP 404；`/api/v1/info` 会列出当前开放的业务接口。其他配置支持 CPU、内存、Spark 指标的公开控制，历史保留时长、采样频率、跨域来源和请求限流。错误或未知配置阻止服务启动，不会擅自重置为公开配置。

## 接口

- `/api/v1/status`：服务器状态、在线人数、TPS/MSPT、可选指标。
- `/api/v1/players`：可选的在线玩家名字和延迟。
- `/api/v1/history`：内存中的近期时间序列。
- `/api/v1/chat?limit=20`：可选的近期公开聊天记录，默认 20 条，实际不能超过管理员配置的 `max_messages`。
- `/api/v1/info`、`/healthz`、`/openapi.json`：功能发现、健康检查和标准规范。

聊天仅保留未取消、同步、接收者涵盖全部在线玩家的 Endstone 广播事件；**无法观察后续 Script API 的消息取消**，依赖这类后置审核的服务器应维持关闭。聊天重启即清空，不存储 UUID、IP 或设备信息。

标准 HTTP 行为包括 GET/HEAD/OPTIONS、CORS、ETag/304、429 Retry-After 和机器可读错误格式。快照过期时返回 503，而不是伪造离线数据。默认公开的 API 完全无需身份验证，HTTPS 建议由代理提供。

此项目采用全新的 v1 协议，**不兼容旧 `/data` 或 show-status JSON**。许可证 GPL-3.0-only。
