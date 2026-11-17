# OMNI CLI ↔ 后端协议（v1）

OMNI CLI 只是客户端：调试/烧录/总线类命令的状态都在后端进程里。CLI 与后端
可以独立发版（CLI 作为 submodule 或独立安装包），因此需要一份显式契约来避免
"命令写到一半发现后端不认"。

## 1. 服务发现

后端启动后把自己的 `{service, pid, host, port, version, protocol}` 写入用户级
注册表文件，CLI 读取它来发现并复用**同一个**后端进程（不要另起一个去抢探针）。

| 服务 | 注册表文件 | 环境变量覆盖 |
| --- | --- | --- |
| `link`（探针/烧录/RTT/调试） | `%LOCALAPPDATA%\omni-link\backend.json` | `OMNI_STATE_DIR` |
| `bus`（串口/网络/Modbus/USB，规划中） | `%LOCALAPPDATA%\omni-bus\backend.json` | `OMNI_BUS_STATE_DIR` |

注册表示例：

```json
{
  "service": "link",
  "pid": 17904,
  "host": "127.0.0.1",
  "port": 57123,
  "version": "0.3.1",
  "protocol": 1,
  "started_at": "2026-11-17T07:19:48+00:00"
}
```

CLI 侧的端口优先级：`--backend-port` → 注册表 → CLI 自己的常驻后端记录
（`.backend_runtime.json`）→ `backend.config.json` 的开发端口 → `OMNI_BACKEND_PORT`。

## 2. 协议版本与兼容

- 后端在 `GET /api/health` 返回 `{status, pid, ws_connections, service, protocol}`。
- `protocol` 是整数，当前为 `1`；只增不改语义，新增字段/接口不算破坏性变更。
- CLI 声明 `BACKEND_PROTOCOL = 1`：
  - 响应缺 `protocol`（老后端）→ 按兼容处理；
  - `protocol > BACKEND_PROTOCOL` → stderr 提示"建议升级 OMNI CLI"，不阻断；
  - 兼容窗口：CLI 至少支持最近 N 个后端协议版本，超出窗口后新命令直接报错并给出升级指引。
- 破坏性改动（删除/改变既有字段或路由语义）必须递增 `protocol`。

## 3. 生命周期接口

- `GET /api/health`：健康探测（CLI 用它区分"没进程"与"进程僵死"）。
- `POST /api/system/shutdown`：优雅关停；后端先在响应后触发 uvicorn 退出，
  并带看门狗兜底强退（避免探针清理卡死时进程不消失）。
- CLI `omni backend stop` 在拿不到优雅关停时按端口定位进程树强杀。

## 4. 流式数据

CLI 不订阅 WebSocket（零第三方依赖），所有流式数据都用"后端环形缓存 + seq 游标 + 轮询"：

- 后端把接收数据存进有上限的环形缓存，并维护 `oldest_seq` / `next_seq` / `dropped_chunks`。
- CLI 用 `--since <seq>` 拉取，返回 `{chunks, oldest_seq, next_seq, cursor, lost, truncated, session_reset}`；
  `lost=true` 表示请求的游标已被覆盖，`session_reset=true` 表示后端重启后 seq 重新开始。
- CLI 的 `--follow` 只是把上一次的 `cursor` 当成下一次的 `since`，间隔默认 0.2s。
- 现有实现：`omni rtt read --follow`（后端 `core/rtt_backend.py::read_capture`）。总线类命令
  （serial/net）沿用同一契约，见后续版本的 `omni serial read --follow`。

## 5. 错误约定

- HTTP 4xx/5xx 的 `detail` 直接展示给用户，命令退出码 1。
- 端口可连但 HTTP 超时 → "后端僵死/被硬件操作阻塞"，引导 `omni backend stop`。
- 端口无监听 → 引导 `omni backend start` 或 `--managed`（仅一次性命令可用）。
