# OMNI CLI ↔ 后端协议（v1）

协议文档的唯一来源在**后端仓库**，本文件只做指引，避免两份漂移：

- 仓库内 submodule：`vendor/omni-backend/docs/protocol.md`
- 独立克隆：`<workspace>/omni-backend/docs/protocol.md`
- 线上：<https://github.com/LuckkMaker/omni-backend/blob/dev/docs/protocol.md>

该文档覆盖：

1. 服务发现（`link` / `bus` 各自的 `backend.json` 登记表与状态目录环境变量）；
2. 启动握手（stdout 首行 `{"port": N}`）与端口优先级；
3. 协议版本与兼容窗口（`protocol` 只增不改语义）；
4. 生命周期接口（`/api/health`、`/api/system/shutdown` 与看门狗）；
5. 流式数据契约（环形缓存 + `--since` 游标 + `--follow`）；
6. 错误约定与两个服务的边界。

CLI 侧与协议相关的实现：

| 位置 | 作用 |
| --- | --- |
| `omni_cli/client.py` | HTTP 客户端、`BACKEND_PROTOCOL`、进程生命周期（常驻/托管） |
| `omni_cli/settings.py` | 服务定义（`ServiceInfo`）、登记表/状态目录/后端位置解析 |
| `omni_cli/autoinstall.py` | 已安装 GUI 的后端目录自动探测 |
| `omni_cli/runtime.py` | 命令组 → 服务映射、`--service` 覆盖、`--managed` 限制 |

修改协议（新增字段/路由、调整语义）时：先改后端仓库的 `docs/protocol.md` 与
`PROTOCOL_VERSION`，再同步 CLI 的 `BACKEND_PROTOCOL`。
