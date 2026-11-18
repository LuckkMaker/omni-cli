"""网络收发命令组（bus 服务）。

三种网络角色与 GUI 的网络页共用同一后端实例与接收环形缓存：

- ``tcp-server``：本机监听，可向指定/全部客户端发送，多客户端时按 ``--peer`` 过滤
- ``tcp-client``：主动连接远端服务器
- ``udp``：绑定本地端口收发，支持广播

接收统一走 ``omni net read --kind ...``（游标增量 / ``--wait`` 同步等读 /
``--follow`` 持续跟踪），与 ``omni serial read`` 共用 commands/_stream.py。
"""

import argparse
import time

from ..client import ApiClient
from ..formatting import ctx_error, print_table
from ..options import common_parser
from ..runtime import finish, run_command
from ._stream import run_cursor_stream

KINDS = ("tcp-server", "tcp-client", "udp")


def add_parser(sub):
    p = sub.add_parser("net", help="网络调试（TCP 服务器/客户端、UDP；bus 服务）")
    ps = p.add_subparsers(dest="net_sub", metavar="<sub>")

    # ── 本机信息 ──
    s = ps.add_parser("interfaces", parents=[common_parser()], help="列出本机 IPv4 地址与主机名")
    s.set_defaults(func=run_command(_interfaces))

    # ── TCP Server ──
    s = ps.add_parser("tcp-server", help="TCP 服务器（监听 / 发送 / 客户端管理）")
    ss = s.add_subparsers(dest="net_tcp_server_sub", metavar="<sub>")

    t = ss.add_parser("start", parents=[common_parser()], help="开始监听")
    t.add_argument("--port", type=int, required=True, help="监听端口（1-65535）")
    t.add_argument("--host", default="0.0.0.0", help="绑定地址（默认 0.0.0.0 全部网卡）")
    t.set_defaults(func=run_command(_ts_start))

    t = ss.add_parser("stop", parents=[common_parser()], help="停止监听")
    t.set_defaults(func=run_command(_ts_stop))

    t = ss.add_parser("status", parents=[common_parser()],
                      help="查询状态与已连接客户端（含收发字节数）")
    t.set_defaults(func=run_command(_ts_status))

    t = ss.add_parser("send", parents=[common_parser()],
                      help="向客户端发送（--target 指定 id，默认广播）")
    t.add_argument("--text", metavar="TEXT", help="发送文本（默认追加换行）")
    t.add_argument("--hex", metavar="HEX", help="发送十六进制字节")
    t.add_argument("--file", metavar="PATH", help="发送文件原始字节")
    t.add_argument("--no-newline", action="store_true", help="--text 不追加换行")
    t.add_argument("--target", action="append", metavar="ID",
                   help="目标客户端 id（可重复；默认广播给全部）")
    t.set_defaults(func=run_command(_ts_send))

    t = ss.add_parser("kick", parents=[common_parser()], help="断开指定客户端")
    t.add_argument("--id", required=True, metavar="ID", help="客户端 id（见 status）")
    t.set_defaults(func=run_command(_ts_kick))

    t = ss.add_parser("echo", parents=[common_parser()], help="开启/关闭服务端回显（收到即原样回送）")
    t.add_argument("state", choices=("on", "off"), help="on 开启 / off 关闭")
    t.set_defaults(func=run_command(_ts_echo))

    t = ss.add_parser("auto-send", help="定时循环发送（后端持有定时器，关掉终端仍继续）")
    a = t.add_subparsers(dest="net_ts_auto_sub", metavar="<sub>")
    x = a.add_parser("start", parents=[common_parser()], help="启动定时发送")
    x.add_argument("--hex", required=True, metavar="HEX", help="定时发送的十六进制载荷")
    x.add_argument("--interval", type=int, default=1000, metavar="MS", help="发送间隔毫秒（默认 1000）")
    x.add_argument("--target", action="append", metavar="ID",
                   help="目标客户端 id（可重复；默认广播）")
    x.set_defaults(func=run_command(_ts_auto_start))
    x = a.add_parser("stop", parents=[common_parser()], help="停止定时发送（返回发送/失败计数）")
    x.set_defaults(func=run_command(_ts_auto_stop))

    # ── TCP Client ──
    s = ps.add_parser("tcp-client", help="TCP 客户端（连接远端服务器）")
    ss = s.add_subparsers(dest="net_tcp_client_sub", metavar="<sub>")

    t = ss.add_parser("connect", parents=[common_parser()], help="连接远端服务器")
    t.add_argument("--host", required=True, help="目标地址（IP 或域名）")
    t.add_argument("--port", type=int, required=True, help="目标端口")
    t.set_defaults(func=run_command(_tc_connect))

    t = ss.add_parser("disconnect", parents=[common_parser()], help="断开连接")
    t.set_defaults(func=run_command(_tc_disconnect))

    t = ss.add_parser("status", parents=[common_parser()], help="查询连接状态与收发统计")
    t.set_defaults(func=run_command(_tc_status))

    t = ss.add_parser("send", parents=[common_parser()], help="向服务器发送数据")
    t.add_argument("--text", metavar="TEXT", help="发送文本（默认追加换行）")
    t.add_argument("--hex", metavar="HEX", help="发送十六进制字节")
    t.add_argument("--file", metavar="PATH", help="发送文件原始字节")
    t.add_argument("--no-newline", action="store_true", help="--text 不追加换行")
    t.set_defaults(func=run_command(_tc_send))

    t = ss.add_parser("auto-send", help="定时循环发送（后端持有定时器，关掉终端仍继续）")
    a = t.add_subparsers(dest="net_tc_auto_sub", metavar="<sub>")
    x = a.add_parser("start", parents=[common_parser()], help="启动定时发送")
    x.add_argument("--hex", required=True, metavar="HEX", help="定时发送的十六进制载荷")
    x.add_argument("--interval", type=int, default=1000, metavar="MS", help="发送间隔毫秒（默认 1000）")
    x.set_defaults(func=run_command(_tc_auto_start))
    x = a.add_parser("stop", parents=[common_parser()], help="停止定时发送（返回发送/失败计数）")
    x.set_defaults(func=run_command(_tc_auto_stop))

    # ── UDP ──
    s = ps.add_parser("udp", help="UDP 收发（绑定本地端口 / 发送数据报）")
    ss = s.add_subparsers(dest="net_udp_sub", metavar="<sub>")

    t = ss.add_parser("start", parents=[common_parser()], help="绑定本地端口开始接收")
    t.add_argument("--port", type=int, required=True, help="本地端口（1-65535）")
    t.add_argument("--host", default="0.0.0.0", help="本地绑定地址（默认 0.0.0.0）")
    t.set_defaults(func=run_command(_udp_start))

    t = ss.add_parser("stop", parents=[common_parser()], help="停止接收")
    t.set_defaults(func=run_command(_udp_stop))

    t = ss.add_parser("status", parents=[common_parser()], help="查询状态与收发统计")
    t.set_defaults(func=run_command(_udp_status))

    t = ss.add_parser("send", parents=[common_parser()], help="发送数据报到目标地址")
    t.add_argument("--to", required=True, metavar="HOST", help="目标地址（支持广播地址）")
    t.add_argument("--port", type=int, required=True, help="目标端口")
    t.add_argument("--text", metavar="TEXT", help="发送文本（默认追加换行）")
    t.add_argument("--hex", metavar="HEX", help="发送十六进制字节")
    t.add_argument("--file", metavar="PATH", help="发送文件原始字节")
    t.add_argument("--no-newline", action="store_true", help="--text 不追加换行")
    t.set_defaults(func=run_command(_udp_send))

    t = ss.add_parser("echo", parents=[common_parser()], help="开启/关闭回声（收到即回给来源）")
    t.add_argument("state", choices=("on", "off"), help="on 开启 / off 关闭")
    t.set_defaults(func=run_command(_udp_echo))

    t = ss.add_parser("auto-send", help="定时循环发送（后端持有定时器，关掉终端仍继续）")
    a = t.add_subparsers(dest="net_udp_auto_sub", metavar="<sub>")
    x = a.add_parser("start", parents=[common_parser()], help="启动定时发送")
    x.add_argument("--hex", required=True, metavar="HEX", help="定时发送的十六进制载荷")
    x.add_argument("--interval", type=int, default=1000, metavar="MS", help="发送间隔毫秒（默认 1000）")
    x.add_argument("--to", required=True, metavar="HOST", help="目标地址（支持广播）")
    x.add_argument("--port", type=int, required=True, help="目标端口")
    x.set_defaults(func=run_command(_udp_auto_start))
    x = a.add_parser("stop", parents=[common_parser()], help="停止定时发送（返回发送/失败计数）")
    x.set_defaults(func=run_command(_udp_auto_stop))

    # ── TFTP（后端已有实现：板子固件/配置文件的传输通道） ──
    s = ps.add_parser("tftp", help="TFTP 服务器/客户端（给设备传文件、从设备取文件）")
    ss = s.add_subparsers(dest="net_tftp_sub", metavar="<sub>")

    t = ss.add_parser("server-start", parents=[common_parser()],
                      help="启动 TFTP 服务器（设备从这里下载/上传文件）")
    t.add_argument("--root", required=True, metavar="DIR", help="根目录（可读写的文件夹）")
    t.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    t.add_argument("--port", type=int, default=69, help="监听端口（默认 69）")
    t.set_defaults(func=run_command(_tftp_server_start))

    t = ss.add_parser("server-stop", parents=[common_parser()], help="停止 TFTP 服务器")
    t.set_defaults(func=run_command(_tftp_server_stop))

    t = ss.add_parser("server-status", parents=[common_parser()],
                      help="TFTP 服务器状态与进行中的传输")
    t.set_defaults(func=run_command(_tftp_server_status))

    t = ss.add_parser("server-files", parents=[common_parser()], help="列出 TFTP 根目录下的文件")
    t.set_defaults(func=run_command(_tftp_server_files))

    t = ss.add_parser("get", parents=[common_parser()],
                      help="从远端 TFTP 服务器下载文件（设备侧作为服务器时用）")
    t.add_argument("--host", required=True, help="TFTP 服务器地址")
    t.add_argument("--port", type=int, default=69, help="服务器端口（默认 69）")
    t.add_argument("--remote", required=True, metavar="NAME", help="远端文件名")
    t.add_argument("--local", required=True, metavar="PATH", help="本地保存路径")
    t.add_argument("--max-wait", type=int, default=300, metavar="SEC",
                   help="等待传输完成的超时秒数（默认 300）")
    t.set_defaults(func=run_command(_tftp_get))

    t = ss.add_parser("put", parents=[common_parser()],
                      help="上传文件到远端 TFTP 服务器")
    t.add_argument("--host", required=True, help="TFTP 服务器地址")
    t.add_argument("--port", type=int, default=69, help="服务器端口（默认 69）")
    t.add_argument("--local", required=True, metavar="PATH", help="本地文件路径")
    t.add_argument("--remote", required=True, metavar="NAME", help="远端文件名")
    t.add_argument("--max-wait", type=int, default=300, metavar="SEC",
                   help="等待传输完成的超时秒数（默认 300）")
    t.set_defaults(func=run_command(_tftp_put))

    # ── 接收缓冲读取（三种角色共用） ──
    s = ps.add_parser(
        "read", parents=[common_parser()],
        help="读取接收缓冲（--kind 选择角色；一次性 / 同步等读 / 持续跟踪）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  omni net read --kind tcp-server --peer 192.168.1.101:52036\n"
               "  omni net read --kind tcp-client --wait 500 --hex\n"
               "  omni net read --kind udp --follow --out rx.bin",
    )
    s.add_argument("--kind", default="tcp-client", choices=KINDS,
                   help="网络角色（默认 tcp-client）")
    s.add_argument("--peer", metavar="ADDR",
                   help="只取该对端的数据（host:port；TCP Server 多客户端时有用）")
    s.add_argument("--since", type=int, default=None,
                   help="从指定 seq 开始读取（0=从最旧可用数据；默认续读上次位置）")
    s.add_argument("--max-bytes", type=int, default=65536, help="单次最多读取的字节数")
    s.add_argument("--wait", type=int, default=None, metavar="MS",
                   help="同步等读：后端阻塞至多该毫秒数等首批数据（不能与 --follow 同用）")
    s.add_argument("--follow", action="store_true", help="持续跟踪新数据（Ctrl+C 结束）")
    s.add_argument("--interval", type=float, default=0.2, help="follow 轮询间隔秒数")
    s.add_argument("--duration", type=float, default=0.0, help="follow 持续秒数（0=一直）")
    s.add_argument("--out", metavar="FILE", help="把原始字节追加写入文件（二进制）")
    s.add_argument("--encoding", default=None, metavar="ENC",
                   help="按指定编码解码显示（默认原始字节直写 stdout）")
    s.add_argument("--hex", action="store_true", help="以十六进制转储显示")
    s.set_defaults(func=run_command(_read))

    # ── Ping ──
    s = ps.add_parser("ping", parents=[common_parser()], help="ICMP ping（结果统计与丢包率）")
    s.add_argument("host", nargs="?", help="目标 IP 或域名（--last 时可省略）")
    s.add_argument("-n", "--count", type=int, default=4, help="发送次数（默认 4）")
    s.add_argument("-i", "--interval", type=int, default=1000, metavar="MS",
                   help="发送间隔毫秒（默认 1000）")
    s.add_argument("-W", "--timeout-ms", type=int, default=1000, metavar="MS",
                   help="单次回包超时毫秒（默认 1000；注意与全局 --timeout 区分）")
    s.add_argument("-s", "--size", type=int, default=32, help="包长字节（默认 32）")
    s.add_argument("--last", action="store_true",
                   help="不发起新 ping，只查询最近一次的汇总结果")
    s.set_defaults(func=run_command(_ping))


# ── 处理器：本机信息 ────────────────────────────────────────

def _interfaces(c: ApiClient, args):
    data = c.get("/api/network/interfaces")
    if args.json_:
        finish(args, data, "")
        return 0
    ips = data.get("ips") or []
    if not args.quiet:
        print(f"主机名: {data.get('hostname') or '-'}")
        if ips:
            print_table(["本机 IPv4"], [[ip] for ip in ips])
        else:
            print("未发现可用的 IPv4 地址（网线/网卡未就绪？）")
    return 0


# ── 处理器：TCP Server ──────────────────────────────────────

def _ts_start(c: ApiClient, args):
    data = c.post("/api/network/tcp-server/start", {"host": args.host, "port": args.port})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "启动失败")
    finish(args, {"started": True, "host": args.host, "port": args.port},
           f"TCP 服务器已监听 {args.host}:{args.port}")
    return 0


def _ts_stop(c: ApiClient, args):
    data = c.post("/api/network/tcp-server/stop")
    if not data.get("success", True):
        return ctx_error(data.get("error") or "停止失败")
    finish(args, {"stopped": True}, "TCP 服务器已停止")
    return 0


def _ts_status(c: ApiClient, args):
    data = c.get("/api/network/tcp-server/status")
    if args.json_:
        finish(args, data, "")
        return 0
    if args.quiet:
        return 0
    if not data.get("running"):
        print("TCP 服务器未运行。可用 `omni net tcp-server start --port 6000` 启动。")
        return 1
    print(f"监听中  {data.get('host')}:{data.get('port')}  "
          f"客户端 {data.get('clientCount')} 个")
    clients = data.get("clients") or []
    if clients:
        print_table(["客户端 ID", "对端地址", "接收", "发送"],
                    [[cl["clientId"], cl["addr"], cl["rx"], cl["tx"]] for cl in clients])
    cap = data.get("capture") or {}
    if cap:
        print(f"接收缓冲: {cap.get('buffered_bytes', 0)} 字节"
              f"（oldest_seq={cap.get('oldest_seq')} next_seq={cap.get('next_seq')}"
              f" dropped={cap.get('dropped_chunks')} 批）")
    return 0


def _ts_send(c: ApiClient, args):
    data, err = _payload(args)
    if err:
        return err
    resp = c.post("/api/network/tcp-server/send",
                  {"data": data.hex().upper(), "targets": args.target})
    if not resp.get("success", True):
        return ctx_error(resp.get("error") or "发送失败")
    scope = "全部客户端" if not args.target else f"{len(args.target)} 个客户端"
    finish(args, {"sent": len(data), "targets": args.target},
           f"已发送 {len(data)} 字节到{scope}")
    return 0


def _ts_kick(c: ApiClient, args):
    data = c.post("/api/network/tcp-server/kick", {"client_id": args.id})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "踢出失败")
    finish(args, {"kicked": args.id}, f"已断开客户端 {args.id}")
    return 0


def _ts_echo(c: ApiClient, args):
    data = c.post("/api/network/tcp-server/echo", {"enabled": args.state == "on"})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "设置失败")
    finish(args, {"echo": args.state == "on"},
           f"服务端回显已{'开启' if args.state == 'on' else '关闭'}")
    return 0


# ── 处理器：TCP Client ──────────────────────────────────────

def _tc_connect(c: ApiClient, args):
    data = c.post("/api/network/tcp-client/connect", {"host": args.host, "port": args.port})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "连接失败")
    finish(args, {"connected": True, "host": args.host, "port": args.port},
           f"已连接 {args.host}:{args.port}")
    return 0


def _tc_disconnect(c: ApiClient, args):
    data = c.post("/api/network/tcp-client/disconnect")
    if not data.get("success", True):
        return ctx_error(data.get("error") or "断开失败")
    finish(args, {"disconnected": True}, "已断开 TCP 连接")
    return 0


def _tc_status(c: ApiClient, args):
    data = c.get("/api/network/tcp-client/status")
    if args.json_:
        finish(args, data, "")
        return 0
    if args.quiet:
        return 0
    if not data.get("running"):
        print("TCP 客户端未连接。可用 `omni net tcp-client connect --host <ip> --port <p>` 连接。")
        return 1
    print(f"已连接  {data.get('host')}:{data.get('port')}  "
          f"接收 {data.get('rx')} 字节 / 发送 {data.get('tx')} 字节")
    cap = data.get("capture") or {}
    if cap:
        print(f"接收缓冲: {cap.get('buffered_bytes', 0)} 字节"
              f"（oldest_seq={cap.get('oldest_seq')} next_seq={cap.get('next_seq')}）")
    return 0


def _tc_send(c: ApiClient, args):
    data, err = _payload(args)
    if err:
        return err
    resp = c.post("/api/network/tcp-client/send", {"data": data.hex().upper()})
    if not resp.get("success", True):
        return ctx_error(resp.get("error") or "发送失败")
    finish(args, {"sent": len(data)}, f"已发送 {len(data)} 字节")
    return 0


# ── 处理器：UDP ────────────────────────────────────────────

def _udp_start(c: ApiClient, args):
    data = c.post("/api/network/udp/start",
                  {"local_host": args.host, "local_port": args.port})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "启动失败")
    finish(args, {"started": True, "host": args.host, "port": args.port},
           f"UDP 已绑定 {args.host}:{args.port}")
    return 0


def _udp_stop(c: ApiClient, args):
    data = c.post("/api/network/udp/stop")
    if not data.get("success", True):
        return ctx_error(data.get("error") or "停止失败")
    finish(args, {"stopped": True}, "UDP 已停止")
    return 0


def _udp_status(c: ApiClient, args):
    data = c.get("/api/network/udp/status")
    if args.json_:
        finish(args, data, "")
        return 0
    if args.quiet:
        return 0
    if not data.get("running"):
        print("UDP 未运行。可用 `omni net udp start --port 5000` 启动。")
        return 1
    print(f"已绑定  {data.get('localHost')}:{data.get('localPort')}  "
          f"接收 {data.get('rx')} 字节 / 发送 {data.get('tx')} 字节")
    cap = data.get("capture") or {}
    if cap:
        print(f"接收缓冲: {cap.get('buffered_bytes', 0)} 字节"
              f"（oldest_seq={cap.get('oldest_seq')} next_seq={cap.get('next_seq')}）")
    return 0


def _udp_send(c: ApiClient, args):
    data, err = _payload(args)
    if err:
        return err
    resp = c.post("/api/network/udp/send",
                  {"data": data.hex().upper(), "target_host": args.to,
                   "target_port": args.port})
    if not resp.get("success", True):
        return ctx_error(resp.get("error") or "发送失败")
    finish(args, {"sent": len(data), "to": f"{args.to}:{args.port}"},
           f"已发送 {len(data)} 字节到 {args.to}:{args.port}")
    return 0


def _udp_echo(c: ApiClient, args):
    data = c.post("/api/network/udp/echo", {"enabled": args.state == "on"})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "设置失败")
    finish(args, {"echo": args.state == "on"},
           f"UDP 回声已{'开启' if args.state == 'on' else '关闭'}")
    return 0


# ── 处理器：TFTP ───────────────────────────────────────────

def _tftp_server_start(c: ApiClient, args):
    data = c.post("/api/network/tftp/server/start",
                  {"root": args.root, "host": args.host, "port": args.port})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "启动失败")
    finish(args, {"started": True, "root": args.root, "host": args.host, "port": args.port},
           f"TFTP 服务器已启动  {args.host}:{args.port}  根目录 {args.root}")
    return 0


def _tftp_server_stop(c: ApiClient, args):
    data = c.post("/api/network/tftp/server/stop")
    if not data.get("success", True):
        return ctx_error(data.get("error") or "停止失败")
    finish(args, {"stopped": True}, "TFTP 服务器已停止")
    return 0


def _tftp_server_status(c: ApiClient, args):
    data = c.get("/api/network/tftp/server/status")
    if args.json_:
        finish(args, data, "")
        return 0
    if args.quiet:
        return 0
    if not data.get("running"):
        print("TFTP 服务器未运行。可用 `omni net tftp server-start --root <dir>` 启动。")
        return 1
    print(f"运行中  {data.get('host')}:{data.get('port')}  根目录 {data.get('root')}"
          f"  进行中传输 {data.get('activeTransfers', 0)}")
    return 0


def _tftp_server_files(c: ApiClient, args):
    data = c.get("/api/network/tftp/server/files")
    files = data.get("files") or []
    if args.json_:
        finish(args, data, "")
        return 0
    if args.quiet:
        return 0
    if not files:
        print("根目录为空。")
        return 0
    rows = []
    for f in files:
        if isinstance(f, dict):
            rows.append([f.get("name") or "-", f.get("size") if f.get("size") is not None else "-"])
        else:
            rows.append([str(f), "-"])
    print_table(["文件", "大小(字节)"], rows)
    return 0


def _tftp_transfer(c: ApiClient, args, direction: str):
    """发起 TFTP 传输并等待完成。

    后端把传输做成后台任务（POST 立即返回），因此必须轮询
    ``/api/network/tftp/client/status`` 直到 ``busy`` 变 False，
    才能拿到真实结果（transferred / status / error）。
    """
    path = f"/api/network/tftp/client/{direction}"
    data = c.post(path, {"host": args.host, "port": args.port,
                         "remote": args.remote, "local": args.local})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "传输失败")

    deadline = time.time() + max(5.0, float(args.max_wait))
    active: dict = {}
    while time.time() < deadline:
        status = c.get("/api/network/tftp/client/status")
        if not status.get("busy"):
            active = status.get("active") or {}
            break
        time.sleep(0.1)
    else:
        # 超时：不静默成功，提示可用 cancel 清理
        return ctx_error(f"传输未在 {args.max_wait} 秒内完成"
                         "（可执行 `omni net tftp` 相关命令前先 cancel，或加大 --timeout）")

    state = (active or {}).get("status")
    if state == "error":
        return ctx_error((active or {}).get("error") or "传输失败")
    if state == "cancelled":
        return ctx_error("传输已取消")

    transferred = (active or {}).get("transferred")
    total = (active or {}).get("total")
    if direction == "download":
        human = f"已下载 {args.remote} → {args.local}"
    else:
        human = f"已上传 {args.local} → {args.remote}"
    if transferred is not None:
        human += f"（{transferred} 字节"
        human += f" / 共 {total} 字节）" if total else "）"
    finish(args, {"ok": True, "direction": direction, **(active or {})}, human)
    return 0


def _tftp_get(c: ApiClient, args):
    return _tftp_transfer(c, args, "download")


def _tftp_put(c: ApiClient, args):
    return _tftp_transfer(c, args, "upload")


# ── 处理器：定时发送 ───────────────────────────────────────

def _auto_start(c: ApiClient, args, path: str, extra: dict):
    cleaned = args.hex.replace(" ", "").replace("0x", "").replace("0X", "")
    if not cleaned or len(cleaned) % 2:
        return ctx_error("无效的十六进制载荷：需要偶数长度的十六进制串")
    payload = {"data": cleaned.upper(), "interval_ms": args.interval, **extra}
    data = c.post(path, payload)
    if not data.get("success", True):
        return ctx_error(data.get("error") or "启动定时发送失败")
    finish(args, {"started": True, "interval_ms": args.interval, **extra},
           f"定时发送已启动（间隔 {args.interval} ms，载荷 {len(cleaned) // 2} 字节）"
           "——后端持有定时器，关掉终端仍继续")
    return 0


def _auto_stop(c: ApiClient, args, path: str):
    data = c.post(path)
    if not data.get("success", True):
        return ctx_error(data.get("error") or "停止定时发送失败")
    finish(args, data, f"定时发送已停止（发送 {data.get('sent', '?')} 次，"
                       f"失败 {data.get('errors', '?')} 次）")
    return 0


def _ts_auto_start(c: ApiClient, args):
    return _auto_start(c, args, "/api/network/tcp-server/auto-send/start",
                       {"targets": args.target})


def _ts_auto_stop(c: ApiClient, args):
    return _auto_stop(c, args, "/api/network/tcp-server/auto-send/stop")


def _tc_auto_start(c: ApiClient, args):
    return _auto_start(c, args, "/api/network/tcp-client/auto-send/start", {})


def _tc_auto_stop(c: ApiClient, args):
    return _auto_stop(c, args, "/api/network/tcp-client/auto-send/stop")


def _udp_auto_start(c: ApiClient, args):
    return _auto_start(c, args, "/api/network/udp/auto-send/start",
                       {"targets": [f"{args.to}:{args.port}"]})


def _udp_auto_stop(c: ApiClient, args):
    return _auto_stop(c, args, "/api/network/udp/auto-send/stop")


# ── 处理器：接收缓冲读取 ────────────────────────────────────

def _read(c: ApiClient, args):
    if args.wait is not None:
        if args.follow:
            return ctx_error("--wait 是一次性同步等读，不能与 --follow 同用。")
        if args.wait < 0:
            return ctx_error("--wait 不能为负数。")

        def _fetch(since: int, max_bytes: int) -> dict:
            return c.post("/api/network/read", {
                "kind": args.kind,
                "since": since,
                "max_bytes": max_bytes,
                "timeout_ms": int(args.wait),
                "peer": args.peer,
            })
    else:
        def _fetch(since: int, max_bytes: int) -> dict:
            query = (f"kind={args.kind}&since={since}&max_bytes={max_bytes}")
            if args.peer:
                query += f"&peer={args.peer}"
            return c.get(f"/api/network/data?{query}")

    return run_cursor_stream(
        args, _fetch,
        subject=f"网络（{args.kind}）",
        empty_hint=f"（暂无接收数据：确认对端已发送、{args.kind} 已启动，"
                   "或用 --peer 缩小范围）",
        cursor_key=f"net:{args.kind}:{args.peer or '*'}",
        service_name="bus",
    )


# ── 处理器：Ping ───────────────────────────────────────────

def _ping(c: ApiClient, args):
    if args.last:
        if args.host:
            return ctx_error("--last 只查询最近一次结果，不要再指定目标地址。")
        data = c.get("/api/network/ping/status")
        last = data.get("last")
        if not last:
            return ctx_error("还没有 ping 记录。先执行一次 `omni net ping <host>`。")
        return _print_ping_result(args, last)

    if not args.host:
        return ctx_error("需要指定目标地址：omni net ping <host>（或用 --last 查上次结果）")

    resp = c.post("/api/network/ping/start", {
        "host": args.host,
        "count": args.count,
        "interval_ms": args.interval,
        "timeout_ms": args.timeout_ms,
        "size": args.size,
    })
    if not resp.get("success", True):
        return ctx_error(resp.get("error") or "启动失败")

    # 轮询状态直到结束（ping 是短任务；后端按行流式解析，这里等汇总）
    deadline = _now_s() + max(10.0, args.count * (args.interval + args.timeout_ms) / 1000.0 + 5)
    result = None
    while _now_s() < deadline:
        status = c.get("/api/network/ping/status")
        if not status.get("running"):
            result = status.get("last")
            break
        _sleep(0.2)
    if result is None:
        return ctx_error("ping 未在预期时间内结束（可用 `omni net ping --last` 查询结果）")
    return _print_ping_result(args, result)


def _print_ping_result(args, result: dict) -> int:
    if args.json_:
        finish(args, result, "")
        return 0
    if not args.quiet:
        print(f"Ping {result.get('host')}: 发送 {result.get('sent')}  "
              f"接收 {result.get('received')}  丢包 {result.get('lost')} "
              f"({result.get('lossPct')}%)")
        if result.get("received"):
            print(f"时延 min/avg/max = {result.get('min')}/{result.get('avg')}/"
                  f"{result.get('max')} ms")
    return 0 if result.get("received") else 1


# ── 工具 ───────────────────────────────────────────────────

def _payload(args):
    """把 --text / --hex / --file 归一成字节；返回 (data, 错误码或 None)。"""
    sources = [a for a in (args.text, args.hex, args.file) if a]
    if len(sources) != 1:
        return None, ctx_error("需要且只能指定一种数据来源：--text / --hex / --file")
    if args.text is not None:
        data = args.text.encode("utf-8")
        if not args.no_newline:
            data += b"\n"
        return data, None
    if args.hex is not None:
        cleaned = args.hex.replace(" ", "").replace("0x", "").replace("0X", "")
        if not cleaned or len(cleaned) % 2:
            return None, ctx_error("无效的十六进制字节：需要偶数长度的十六进制串")
        try:
            return bytes.fromhex(cleaned), None
        except ValueError as e:
            return None, ctx_error(f"无效的十六进制字节: {e}")
    try:
        with open(args.file, "rb") as f:
            return f.read(), None
    except OSError as e:
        return None, ctx_error(f"无法读取文件 {args.file}: {e}")


def _now_s() -> float:
    return time.time()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
