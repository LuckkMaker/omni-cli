"""流式读取命令（rtt / serial / 将来的 net）的共用游标读取实现。

三个服务的后端返回同一套契约（见 omni-backend/docs/protocol.md §4）::

    {oldest_seq, next_seq, last_seq, cursor, truncated, lost, session_reset,
     dropped_bytes, dropped_chunks, chunks:[{seq, size, data(base64)}]}

CLI 只需记住上一次的 ``cursor`` 作为下一次的 ``since`` 即可连续读取；
``--follow`` 就是把这个循环跑起来，间隔默认 0.2s。

输出模式（互斥的显示路径，数据本体不变）：

- 默认：**原始字节直写 stdout**（二进制安全、可管道重定向；纯 ASCII 日志的
  显示效果与文本模式相同）
- ``--encoding ENC``：按 ENC 增量解码显示（非法字节用替换符；多字节字符跨批
  由增量解码器拼接）
- ``--hex``：经典十六进制转储（偏移 + 十六进制 + ASCII 三列）
- ``--json``：结构化输出（follow 时逐批 JSONL），chunks 保留 base64 无损
- ``--out FILE``：原始字节追加落盘（与上述任一显示模式并存）
"""

import base64
import codecs
import json
import sys
import time

from .. import cursors
from ..formatting import ctx_error


def hexdump(data: bytes, base_offset: int = 0) -> str:
    """经典十六进制转储：偏移 | 十六进制 | ASCII，每行 16 字节。"""
    lines = []
    for off in range(0, len(data), 16):
        row = data[off:off + 16]
        hex_part = " ".join(f"{b:02X}" for b in row)
        # 补齐十六进制列宽，保持 ASCII 列对齐
        hex_part += "   " * (16 - len(row))
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"{base_offset + off:08X}  {hex_part}  {ascii_part}")
    return "\n".join(lines)


def _raw_write(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def since_now(peek) -> int:
    """取当前 ``next_seq``：让 ``--wait`` 等读只等**新**数据，不重放历史。

    ``since=0`` 的语义是「从最旧可用数据开始」，对 ``--follow`` 正确；但
    「发一条、收一条」的等读如果也从头读，第二次调用就会把上一轮的响应
    再吐一遍。等读时改为从当前末尾开始。
    """
    try:
        return int(peek().get("next_seq") or 0)
    except Exception:
        return 0


def _fetch_batches(fetch, since: int, max_bytes: int):
    """调用 fetch 并解出 (响应字典, 原始字节)。fetch 抛 ApiError 由调用方处理。"""
    data = fetch(since, max_bytes)
    raw = b"".join(base64.b64decode(ch.get("data") or "") for ch in (data.get("chunks") or []))
    return data, raw


def run_cursor_stream(args, fetch, *, subject: str, empty_hint: str,
                      cursor_key: str | None = None,
                      service_name: str | None = None) -> int:
    """执行一次（或 --follow 持续）游标读取，返回退出码。

    Args:
        args: 叶子命令的命名空间。使用字段：since / max_bytes / follow /
            interval / duration / out / encoding / hex / json_ / quiet。
        fetch: ``fetch(since, max_bytes) -> dict``，调用方负责拼服务专有的
            URL 与查询参数（如 rtt 的 uid/channel）。
        subject: 文案中的对象名（"RTT" / "串口"），用于警告与提示。
        empty_hint: 无数据时的可执行提示（如“固件未初始化 RTT”/“确认对端已发送”）。
        cursor_key: 游标记忆的键（如 ``serial:cli``）；给出时默认续读上次位置，
            首次从最旧数据开始。显式 ``--since`` 覆盖。
        service_name: 游标所属服务（决定状态目录），默认 link。
    """
    if args.follow and args.json_ and args.quiet:
        print("错误: --quiet 与 --json --follow 不能同时使用（JSONL 需要输出）。", file=sys.stderr)
        return 2
    if args.max_bytes < 1:
        print("错误: --max-bytes 必须 ≥ 1。", file=sys.stderr)
        return 2
    if args.follow and args.interval < 0.05:
        args.interval = 0.05

    decoder = None
    if args.encoding and not args.json_:
        try:
            decoder = codecs.getincrementaldecoder(args.encoding)(errors="replace")
        except LookupError:
            print(f"错误: 不支持的编码: {args.encoding}", file=sys.stderr)
            return 2

    out = None
    if args.out:
        try:
            out = open(args.out, "ab")
        except OSError as e:
            print(f"错误: 无法打开输出文件 {args.out}: {e}", file=sys.stderr)
            return 1

    # 起始游标：显式 --since（含 0）优先 → 记忆的上次位置 → 最旧数据
    explicit_since = getattr(args, "since", None)
    if explicit_since is not None:
        since = max(0, int(explicit_since))
    elif cursor_key:
        since = cursors.load_cursor(cursor_key, service_name or "link")
    else:
        since = 0
    deadline = time.time() + args.duration if (args.follow and args.duration > 0) else None
    read_any = False
    session_reset_warned = False
    follow = bool(args.follow)
    tail = ""

    try:
        while True:
            data, raw = _fetch_batches(fetch, since, int(args.max_bytes))
            if data.get("success") is False:
                # 服务级错误（如“串口实例尚未打开”）：直接给出后端的可执行提示
                print(f"错误: {data.get('error') or '读取失败'}", file=sys.stderr)
                return 1
            read_any = read_any or bool(raw)

            if out is not None and raw:
                out.write(raw)
                out.flush()

            # 警告的处理必须区分输出模式：
            # - --json：绝不写 stderr（调用方常把 2>&1 合并捕获，会把警告混进 JSON），
            #   改为放进 JSON 的 warning 字段，stdout 始终是纯 JSON/JSONL。
            # - 人类可读模式：警告写 stderr，不污染正文。
            warnings: list[str] = []
            if data.get("session_reset") and not session_reset_warned:
                warnings.append("指定的 --since 超出当前后端进程的序号"
                                "（后端重启后 seq 从 1 开始），已从当前缓存最旧数据读取。")
                session_reset_warned = True
            if data.get("lost"):
                warnings.append(f"{subject} 环形缓存已覆盖部分历史数据（lost），"
                                "如需完整数据请及时 --follow 或提高后端缓存容量。")

            if warnings and not args.json_ and not args.quiet:
                for warning in warnings:
                    print(f"警告: {warning}", file=sys.stderr)

            if args.json_:
                # follow 模式：有数据/有警告时才输出一行，避免空行刷屏；
                # 非 follow 模式：即使没有数据也输出一个完整对象。
                if raw or warnings or not follow:
                    payload = {k: v for k, v in data.items() if k != "chunks"}
                    payload["chunks"] = data.get("chunks") or []
                    if warnings:
                        payload["warning"] = "；".join(warnings)
                    if not args.quiet:
                        # follow 模式输出 JSONL（每批一行），便于流式消费
                        print(json.dumps(payload, ensure_ascii=False, indent=None if follow else 2))
            elif raw and not args.quiet:
                if args.hex:
                    print(hexdump(raw))
                elif decoder is not None:
                    text = decoder.decode(raw)
                    if text:
                        sys.stdout.write(text)
                        sys.stdout.flush()
                        tail = text[-1:]
                else:
                    # 默认：原始字节直写（二进制安全，不追加任何额外字节）
                    _raw_write(raw)

            since = int(data.get("cursor", since))
            if cursor_key:
                cursors.save_cursor(cursor_key, since, service_name or "link")
            if not follow:
                break
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        # follow 是无限流，Ctrl+C 是正常结束方式
        pass
    finally:
        if out is not None:
            out.close()

    if not args.json_ and not args.quiet:
        if not read_any:
            print(empty_hint, file=sys.stderr)
        elif decoder is not None:
            # 刷出增量解码器可能残留的半个多字节字符
            rest = decoder.decode(b"", final=True)
            if rest:
                sys.stdout.write(rest)
                tail = rest[-1:]
            # 数据不以换行结尾时补一个换行，避免命令提示符贴在日志末尾；
            # 已换行则不补，防止多出空行。
            if tail not in ("", "\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
        # 原始字节与 hex 模式不补任何字节：保证 stdout 是纯净的数据流
    if out is not None and not args.json_ and not args.quiet:
        print(f"已追加写入: {args.out}", file=sys.stderr)
    return 0


__all__ = ["hexdump", "run_cursor_stream", "since_now"]
