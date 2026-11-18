"""串口收发命令组（bus 服务）。

数据链路与 GUI 的串口页共用同一后端实例与接收环形缓存；
CLI 按 seq 游标增量拉取（`--follow` 持续跟踪），多客户端互不影响。
"""

import argparse
import base64
import os
import sys

from ..client import ApiClient
from ..formatting import ctx_error, print_table
from ..options import common_parser
from ..runtime import finish, run_command
from ._stream import run_cursor_stream


def add_parser(sub):
    p = sub.add_parser("serial", help="串口收发（列出 / 打开 / 发送 / 接收缓冲读取；bus 服务）")
    ps = p.add_subparsers(dest="serial_sub", metavar="<sub>")

    s = ps.add_parser("ports", parents=[common_parser()], help="列出主机串口（COM 口）")
    s.set_defaults(func=run_command(_ports))

    s = ps.add_parser("open", parents=[common_parser()], help="打开串口")
    s.add_argument("--port", required=True, metavar="COM", help="端口名，如 COM3")
    s.add_argument("--baud", type=int, default=115200, help="波特率（默认 115200）")
    s.add_argument("--bytesize", type=int, default=8, choices=(5, 6, 7, 8), help="数据位（默认 8）")
    s.add_argument("--parity", default="N", choices=("N", "E", "O", "M", "S"),
                   help="校验位：N 无 / E 偶 / O 奇 / M 标记 / S 空格（默认 N）")
    s.add_argument("--stopbits", type=float, default=1, choices=(1, 1.5, 2), help="停止位（默认 1）")
    s.add_argument("--flow", default="none", choices=("none", "xonxoff", "rtscts"),
                   help="流控：none / xonxoff 软件流控 / rtscts 硬件流控（默认 none）")
    s.add_argument("--rts", action="store_true", help="打开时置位 RTS（默认清除）")
    s.add_argument("--dtr", action="store_true", help="打开时置位 DTR（默认清除）")
    s.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli；多口并行时各自指定）")
    s.set_defaults(func=run_command(_open))

    s = ps.add_parser("status", parents=[common_parser()], help="查询串口状态（含接收缓冲统计）")
    s.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    s.set_defaults(func=run_command(_status))

    s = ps.add_parser("close", parents=[common_parser()], help="关闭串口（接收缓存保留，仍可继续读取）")
    s.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    s.set_defaults(func=run_command(_close))

    s = ps.add_parser("send", parents=[common_parser()], help="发送数据（文本 / 十六进制 / 二进制文件）")
    s.add_argument("--text", metavar="TEXT", help="发送文本（默认追加换行，用 --no-newline 关闭）")
    s.add_argument("--hex", metavar="HEX", help="发送十六进制字节，如 48656C6C6F")
    s.add_argument("--file", metavar="PATH", help="发送文件的原始字节（二进制）")
    s.add_argument("--no-newline", action="store_true", help="--text 不追加换行符")
    s.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    s.set_defaults(func=run_command(_send))

    s = ps.add_parser(
        "read", parents=[common_parser()],
        help="读取接收缓冲（一次性 / --wait 同步等读 / --follow 持续跟踪）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  omni serial read --id cli                # 读取当前已接收数据\n"
               "  omni serial read --wait 500              # 等待 500ms 内到达的数据\n"
               "  omni serial read --follow --out rx.bin   # 持续接收并落盘\n"
               "  omni serial send --text AT && omni serial read --wait 300",
    )
    s.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    s.add_argument("--since", type=int, default=None,
                   help="从指定 seq 开始读取（0=从最旧可用数据；默认续读上次位置，"
                        "首次读则从最旧开始）")
    s.add_argument("--max-bytes", type=int, default=65536,
                   help="单次最多读取的字节数（默认 65536）")
    s.add_argument("--wait", type=int, default=None, metavar="MS",
                   help="同步等读：后端阻塞至多该毫秒数等首批数据（AT 命令式交互；"
                        "不能与 --follow 同用）")
    s.add_argument("--follow", action="store_true", help="持续跟踪新数据（Ctrl+C 结束）")
    s.add_argument("--interval", type=float, default=0.2,
                   help="follow 轮询间隔秒数（默认 0.2）")
    s.add_argument("--duration", type=float, default=0.0,
                   help="follow 持续秒数（默认 0=一直跟踪到 Ctrl+C）")
    s.add_argument("--out", metavar="FILE", help="把原始字节追加写入文件（二进制）")
    s.add_argument("--encoding", default=None, metavar="ENC",
                   help="按指定编码解码显示（默认原始字节直写 stdout；"
                        "非法字节用替换符，--json 的 base64 无损）")
    s.add_argument("--hex", action="store_true",
                   help="以十六进制转储显示（偏移 + 十六进制 + ASCII）")
    s.set_defaults(func=run_command(_read))

    s = ps.add_parser("record", help="录制接收数据（后端执行，关掉终端仍继续）")
    rs = s.add_subparsers(dest="serial_record_sub", metavar="<sub>")

    t = rs.add_parser("start", parents=[common_parser()], help="开始录制接收数据到文件")
    t.add_argument("--out", required=True, metavar="PATH", help="录制文件路径（.dat，追加写入）")
    t.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    t.set_defaults(func=run_command(_record_start))

    t = rs.add_parser("stop", parents=[common_parser()], help="停止录制（返回录制字节数）")
    t.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    t.set_defaults(func=run_command(_record_stop))

    t = rs.add_parser("status", parents=[common_parser()], help="查询录制状态")
    t.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    t.set_defaults(func=run_command(_record_status))

    s = ps.add_parser("auto-send", help="定时循环发送（后端持有定时器，关掉终端仍继续）")
    a = s.add_subparsers(dest="serial_auto_sub", metavar="<sub>")

    t = a.add_parser("start", parents=[common_parser()], help="启动定时发送")
    t.add_argument("--hex", required=True, metavar="HEX", help="定时发送的十六进制载荷")
    t.add_argument("--interval", type=int, default=1000, metavar="MS",
                   help="发送间隔毫秒（默认 1000）")
    t.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    t.set_defaults(func=run_command(_auto_send_start))

    t = a.add_parser("stop", parents=[common_parser()], help="停止定时发送（返回发送/失败计数）")
    t.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    t.set_defaults(func=run_command(_auto_send_stop))

    t = a.add_parser("update", parents=[common_parser()], help="在线调整载荷/间隔")
    t.add_argument("--hex", metavar="HEX", help="新的十六进制载荷（可选）")
    t.add_argument("--interval", type=int, metavar="MS", help="新的间隔毫秒（可选）")
    t.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    t.set_defaults(func=run_command(_auto_send_update))

    s = ps.add_parser("update", parents=[common_parser()],
                      help="在线更新串口参数（无需关闭重开）")
    s.add_argument("--baud", type=int, help="波特率")
    s.add_argument("--rts", action="store_true", help="置位 RTS")
    s.add_argument("--no-rts", action="store_true", help="清除 RTS")
    s.add_argument("--dtr", action="store_true", help="置位 DTR")
    s.add_argument("--no-dtr", action="store_true", help="清除 DTR")
    s.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID（默认 cli）")
    s.set_defaults(func=run_command(_update))


# ── 处理器 ──────────────────────────────────────────────────

def _ports(c: ApiClient, args):
    data = c.get("/api/serial/ports")
    ports = data.get("ports") or []
    if args.json_:
        finish(args, data, "")
        return 0
    if not ports:
        if not args.quiet:
            print("未发现主机串口。")
        return 0
    rows = []
    for p in ports:
        vid_pid = f"{p.get('vid')}:{p.get('pid')}" if p.get("vid") else "-"
        rows.append([p.get("device") or "-", p.get("description") or "-",
                     vid_pid, p.get("manufacturer") or "-"])
    if not args.quiet:
        print_table(["端口", "描述", "VID:PID", "厂商"], rows)
    return 0


def _open(c: ApiClient, args):
    payload = {
        "connection_id": args.id,
        "port": args.port,
        "baudrate": args.baud,
        "bytesize": args.bytesize,
        "parity": args.parity,
        "stopbits": args.stopbits,
        "flow_control": args.flow,
        "rts": bool(args.rts),
        "dtr": bool(args.dtr),
    }
    data = c.post("/api/serial/open", payload)
    if not data.get("success", True):
        return ctx_error(data.get("error") or "打开串口失败")
    human = (f"串口已打开: {args.port} @ {args.baud} "
             f"{args.bytesize}{args.parity}{int(args.stopbits)} 流控={args.flow} "
             f"RTS={int(bool(args.rts))} DTR={int(bool(args.dtr))}（实例 {args.id}）")
    finish(args, {"opened": True, "port": args.port, "baudrate": args.baud,
                  "id": args.id}, human)
    return 0


def _status(c: ApiClient, args):
    data = c.get(f"/api/serial/status?connection_id={args.id}")
    if args.json_:
        finish(args, data, "")
        return 0
    if not data.get("connected"):
        print(f"串口未打开（实例 {args.id}）。可用 `omni serial open --port COMx` 打开。")
        return 1
    cap = data.get("capture") or {}
    print(f"已打开  {data.get('port')}  @ {data.get('baudrate')} "
          f"{data.get('bytesize')}{data.get('parity')}{int(data.get('stopbits', 1))}"
          f"（实例 {args.id}）")
    if cap:
        print(f"接收缓冲: {cap.get('buffered_bytes', 0)} 字节"
              f"（oldest_seq={cap.get('oldest_seq')} next_seq={cap.get('next_seq')}"
              f" dropped={cap.get('dropped_chunks')} 批）")
    return 0


def _close(c: ApiClient, args):
    data = c.post("/api/serial/close", {"connection_id": args.id})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "关闭串口失败")
    finish(args, {"closed": True, "id": args.id},
           f"串口已关闭（实例 {args.id}）。接收缓存保留，仍可 `omni serial read` 回看。")
    return 0


def _send(c: ApiClient, args):
    sources = [a for a in (args.text, args.hex, args.file) if a]
    if len(sources) != 1:
        return ctx_error("需要且只能指定一种数据来源：--text / --hex / --file")
    if args.text is not None:
        data = args.text.encode("utf-8")
        if not args.no_newline:
            data += b"\n"
        kind = "文本"
    elif args.hex is not None:
        try:
            data = _parse_hex(args.hex)
        except ValueError as e:
            return ctx_error(f"无效的十六进制字节: {e}")
        kind = "十六进制"
    else:
        try:
            with open(args.file, "rb") as f:
                data = f.read()
        except OSError as e:
            return ctx_error(f"无法读取文件 {args.file}: {e}")
        kind = "文件"

    resp = c.post("/api/serial/send",
                  {"connection_id": args.id, "data": data.hex().upper()})
    if not resp.get("success", True):
        return ctx_error(resp.get("error") or "发送失败")
    preview = " ".join(f"{b:02X}" for b in data[:16]) + (" …" if len(data) > 16 else "")
    finish(args, {"sent": len(data), "id": args.id, "kind": kind},
           f"已发送 {len(data)} 字节（{kind}，实例 {args.id}）  [{preview}]")
    return 0


def _read(c: ApiClient, args):
    if args.wait is not None:
        if args.follow:
            return ctx_error("--wait 是一次性同步等读，不能与 --follow 同用。")
        if args.wait < 0:
            return ctx_error("--wait 不能为负数。")

        def _fetch(since: int, max_bytes: int) -> dict:
            return c.post("/api/serial/read", {
                "connection_id": args.id,
                "since": since,
                "max_bytes": max_bytes,
                "timeout_ms": int(args.wait),
            })
    else:
        def _fetch(since: int, max_bytes: int) -> dict:
            return c.get(f"/api/serial/data?connection_id={args.id}"
                         f"&since={since}&max_bytes={max_bytes}")

    return run_cursor_stream(
        args, _fetch,
        subject="串口",
        empty_hint="（暂无接收数据：确认对端已发送且波特率/校验位匹配，"
                   "或先执行 `omni serial open`）",
        cursor_key=f"serial:{args.id}",
        service_name="bus",
    )


def _record_start(c: ApiClient, args):
    data = c.post("/api/serial/record/start",
                  {"connection_id": args.id, "path": args.out})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "开始录制失败")
    finish(args, {"recording": True, "path": args.out, "id": args.id},
           f"开始录制到 {args.out}（后端落盘，关掉终端仍继续）")
    return 0


def _record_stop(c: ApiClient, args):
    data = c.post("/api/serial/record/stop", {"connection_id": args.id})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "停止录制失败")
    written = data.get("bytes_written", data.get("bytes"))
    finish(args, data, f"录制已停止（本次 {written if written is not None else '?'} 字节）")
    return 0


def _record_status(c: ApiClient, args):
    data = c.get(f"/api/serial/status?connection_id={args.id}")
    if args.json_:
        finish(args, data, "")
        return 0
    if args.quiet:
        return 0
    rec = data.get("recording") or {}
    if not rec.get("active") and not rec.get("path"):
        print(f"未在录制（实例 {args.id}）。可用 `omni serial record start --out <path>`。")
        return 1
    print(f"录制中  {rec.get('path')}  已写 {rec.get('bytes', 0)} 字节（实例 {args.id}）")
    return 0


def _auto_send_start(c: ApiClient, args):
    cleaned = args.hex.replace(" ", "").replace("0x", "").replace("0X", "")
    if not cleaned or len(cleaned) % 2:
        return ctx_error("无效的十六进制载荷：需要偶数长度的十六进制串")
    data = c.post("/api/serial/auto-send/start",
                  {"connection_id": args.id, "data": cleaned.upper(),
                   "interval_ms": args.interval})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "启动定时发送失败")
    finish(args, {"started": True, "interval_ms": args.interval, "id": args.id},
           f"定时发送已启动（间隔 {args.interval} ms，载荷 {len(cleaned) // 2} 字节，"
           f"实例 {args.id}）——后端持有定时器，关掉终端仍继续")
    return 0


def _auto_send_stop(c: ApiClient, args):
    data = c.post("/api/serial/auto-send/stop", {"connection_id": args.id})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "停止定时发送失败")
    finish(args, data, f"定时发送已停止（发送 {data.get('sent', '?')} 次，"
                       f"失败 {data.get('errors', '?')} 次）")
    return 0


def _auto_send_update(c: ApiClient, args):
    payload = {"connection_id": args.id}
    if args.hex is not None:
        cleaned = args.hex.replace(" ", "").replace("0x", "").replace("0X", "")
        if not cleaned or len(cleaned) % 2:
            return ctx_error("无效的十六进制载荷：需要偶数长度的十六进制串")
        payload["data"] = cleaned.upper()
    if args.interval is not None:
        payload["interval_ms"] = args.interval
    if len(payload) == 1:
        return ctx_error("没有要更新的参数：可用 --hex / --interval")
    data = c.post("/api/serial/auto-send/update", payload)
    if not data.get("success", True):
        return ctx_error(data.get("error") or "更新定时发送失败")
    finish(args, data, f"定时发送已更新（间隔 {data.get('interval_ms', '?')} ms，"
                       f"载荷 {data.get('bytes', '?')} 字节）")
    return 0


def _update(c: ApiClient, args):
    patch: dict = {}
    if args.baud is not None:
        patch["baudrate"] = args.baud
    if args.rts:
        patch["rts"] = True
    if args.no_rts:
        patch["rts"] = False
    if args.dtr:
        patch["dtr"] = True
    if args.no_dtr:
        patch["dtr"] = False
    if not patch:
        return ctx_error("没有要更新的参数：可用 --baud / --rts / --no-rts / --dtr / --no-dtr")
    data = c.post("/api/serial/update", {"connection_id": args.id, **patch})
    if not data.get("success", True):
        return ctx_error(data.get("error") or "更新失败")
    finish(args, {"updated": patch, "id": args.id},
           f"串口参数已更新: {patch}（实例 {args.id}）")
    return 0


def _parse_hex(text: str) -> bytes:
    """解析十六进制字节串：允许空格，长度必须为偶数。"""
    cleaned = text.replace(" ", "").replace("0x", "").replace("0X", "")
    if not cleaned:
        raise ValueError("空字符串")
    if len(cleaned) % 2:
        raise ValueError("十六进制长度必须为偶数")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as e:
        raise ValueError(str(e)) from None
