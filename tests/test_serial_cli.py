"""串口命令组与流式读取共用实现的离线测试（无需硬件、无需后端）。

覆盖：

1. ``commands/_stream.py``（rtt / serial 共用的游标读取循环）：
   原始字节 / 解码 / hex / --json 各输出模式、游标推进、lost 与 session_reset
   警告、--out 落盘、follow 定时结束、success=False 错误路径
2. ``commands/serial.py``：open/send/read/update 的请求载荷与渲染
3. ``cli.py`` 分类帮助：所有顶层命令都归入某一类别，无遗漏

用法::

    python tests/test_serial_cli.py
"""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PY_DIR)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures.append(name)
    print(f"  {status}  {name}" + (f"  ({detail})" if detail and not cond else ""))


class FakeStdout:
    """同时收集文本与原始字节写入的 stdout 替身。"""

    def __init__(self):
        self.buffer = io.BytesIO()
        self._text = io.StringIO()

    def write(self, s):
        return self._text.write(s)

    def flush(self):
        pass

    @property
    def text(self):
        return self._text.getvalue()

    @property
    def raw(self):
        return self.buffer.getvalue()


def _args(**kwargs):
    base = dict(since=0, max_bytes=65536, follow=False, interval=0.2,
                duration=0.0, out=None, encoding=None, hex=False,
                json_=False, quiet=False)
    base.update(kwargs)
    return SimpleNamespace(**base)


def _chunk(seq: int, data: bytes) -> dict:
    return {"seq": seq, "size": len(data),
            "data": base64.b64encode(data).decode("ascii")}


def _resp(chunks, *, next_seq=None, cursor=None, lost=False, session_reset=False,
          success=True, error=None):
    seqs = [c["seq"] for c in chunks]
    return {
        "success": success,
        "error": error,
        "oldest_seq": seqs[0] if seqs else 1,
        "next_seq": next_seq if next_seq is not None else (max(seqs) + 1 if seqs else 1),
        "last_seq": seqs[-1] if seqs else 0,
        "cursor": cursor if cursor is not None else (max(seqs) + 1 if seqs else 1),
        "truncated": False,
        "lost": lost,
        "session_reset": session_reset,
        "dropped_bytes": 0,
        "dropped_chunks": 0,
        "chunks": chunks,
    }


class ScriptedFetch:
    """按脚本依次返回响应的 fetch 替身，记录每次请求的 (since, max_bytes)。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, since, max_bytes):
        self.calls.append((since, max_bytes))
        if not self.responses:
            # 兜底响应续用当前游标，模拟“后端暂无新数据”
            return _resp([], next_seq=since, cursor=since)
        return self.responses.pop(0)


# ── 1. 共用游标读取循环 ─────────────────────────────────────

def test_raw_mode():
    print("[1] 原始字节默认输出")
    from omni_cli.commands import _stream
    fetch = ScriptedFetch([_resp([_chunk(1, b"Hello"), _chunk(2, b" \xe4\xb8\x96")])])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(), fetch, subject="串口", empty_hint="empty")
    check("退出码 0", rc == 0, str(rc))
    check("stdout 原始字节与数据完全一致", out.raw == b"Hello \xe4\xb8\x96", repr(out.raw))
    check("文本通道未写入（原始模式不解码）", out.text == "", repr(out.text))
    check("原始模式不追加换行", not out.raw.endswith(b"\n"))
    check("fetch 只调用一次", len(fetch.calls) == 1)


def test_decode_mode():
    print("[2] --encoding 解码显示（含尾部换行规则）")
    from omni_cli.commands import _stream
    # 数据以换行结尾：不补换行
    fetch = ScriptedFetch([_resp([_chunk(1, b"log line\n")])])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(encoding="utf-8"), fetch,
                                       subject="RTT", empty_hint="empty")
    check("退出码 0", rc == 0)
    check("解码文本一致", out.text == "log line\n", repr(out.text))

    # 数据不以换行结尾：补一个换行（避免提示符贴行尾）
    fetch = ScriptedFetch([_resp([_chunk(1, b"partial")])])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(encoding="utf-8"), fetch,
                                       subject="RTT", empty_hint="empty")
    check("无换行结尾时补一个换行", out.text == "partial\n", repr(out.text))

    # 非法字节显示为替换符（不崩溃）
    fetch = ScriptedFetch([_resp([_chunk(1, b"ab\xff\xfe cd")])])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(encoding="utf-8"), fetch,
                                       subject="RTT", empty_hint="empty")
    check("非法字节以替换符显示且不崩溃", rc == 0 and "\ufffd" in out.text, repr(out.text))


def test_hex_mode():
    print("[3] --hex 十六进制转储")
    from omni_cli.commands import _stream
    data = bytes(range(16)) + b"AB"
    fetch = ScriptedFetch([_resp([_chunk(1, data)])])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(hex=True), fetch,
                                       subject="串口", empty_hint="empty")
    lines = out.text.strip().splitlines()
    check("退出码 0", rc == 0)
    check("第一行偏移 00000000", lines[0].startswith("00000000"), lines[0][:16])
    check("首行 16 字节十六进制", "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F" in lines[0],
          lines[0])
    check("ASCII 列呈现可打印字符", lines[0].endswith(
        "".join(chr(b) if 32 <= b < 127 else "." for b in bytes(range(16)))), lines[0][-20:])
    check("第二行为剩余 2 字节", lines[1].startswith("00000010") and "41 42" in lines[1], lines[1])


def test_json_mode():
    print("[4] --json 结构化输出")
    from omni_cli.commands import _stream
    chunks = [_chunk(1, b"AB"), _chunk(2, b"CD")]
    fetch = ScriptedFetch([_resp(chunks, lost=True)])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(json_=True), fetch,
                                       subject="串口", empty_hint="empty")
    payload = json.loads(out.text)
    check("退出码 0", rc == 0)
    check("chunks 无损保留 base64", payload["chunks"] == chunks)
    check("游标字段保留", payload["cursor"] == 3)
    check("lost 进 warning 字段而非 stderr", "lost" in payload.get("warning", "")
          and err.getvalue() == "", repr(err.getvalue()))


def test_follow_and_cursor():
    print("[5] --follow 游标推进与 duration 结束")
    from omni_cli.commands import _stream
    fetch = ScriptedFetch([
        _resp([_chunk(1, b"AAA")], cursor=2),          # truncated 场景：cursor=2
        _resp([_chunk(2, b"BBB")], next_seq=3, cursor=3),
        _resp([], next_seq=3, cursor=3),                # 空轮
    ])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(follow=True, duration=0.3, interval=0.05),
                                       fetch, subject="串口", empty_hint="empty")
    check("退出码 0", rc == 0)
    check("游标按响应 cursor 推进", [c[0] for c in fetch.calls[:3]] == [0, 2, 3],
          str(fetch.calls))
    check("空轮游标原地等待", all(c[0] == 3 for c in fetch.calls[3:]), str(fetch.calls))
    check("两批数据都输出", out.raw == b"AAABBB", repr(out.raw))


def test_warnings_and_errors():
    print("[6] lost/session_reset 警告与 success=False 错误")
    from omni_cli.commands import _stream
    fetch = ScriptedFetch([_resp([_chunk(3, b"X")], lost=True, session_reset=True)])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(), fetch, subject="串口", empty_hint="empty")
    err_text = err.getvalue()
    check("session_reset 警告进 stderr", "后端重启" in err_text, err_text)
    check("lost 警告含对象名", "串口 环形缓存" in err_text, err_text)

    fetch = ScriptedFetch([_resp([], success=False, error="串口实例 'cli' 尚未打开")])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(), fetch, subject="串口", empty_hint="empty")
    check("success=False 退出码 1", rc == 1, str(rc))
    check("后端错误透传到 stderr", "尚未打开" in err.getvalue(), err.getvalue())

    # 空数据（正常）→ 提示语，退出码 0
    fetch = ScriptedFetch([_resp([], next_seq=1, cursor=1)])
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(), fetch, subject="串口", empty_hint="请先 open")
    check("空数据提示语进 stderr 且退出码 0", rc == 0 and "请先 open" in err.getvalue())

    # follow+json+quiet 冲突
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = _stream.run_cursor_stream(_args(follow=True, json_=True, quiet=True),
                                        ScriptedFetch([]), subject="串口", empty_hint="e")
    check("follow+json+quiet 拒绝", rc == 2, str(rc))


def test_out_file():
    print("[7] --out 落盘（与其他模式并存）")
    from omni_cli.commands import _stream
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rx.bin")
        fetch = ScriptedFetch([
            _resp([_chunk(1, b"AAA")], next_seq=2, cursor=2),
            _resp([_chunk(2, b"BBB")], next_seq=3, cursor=3),
        ])
        out, err = FakeStdout(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = _stream.run_cursor_stream(
                _args(follow=True, duration=0.15, interval=0.05, out=path, quiet=True),
                fetch, subject="串口", empty_hint="empty")
        data = Path(path).read_bytes()
        check("退出码 0", rc == 0)
        check("两批字节按序落盘", data == b"AAABBB", repr(data))
        check("--quiet 抑制了 stdout 输出", out.raw == b"" and out.text == "")


# ── 2. serial 命令组 ────────────────────────────────────────

class StubClient:
    """记录调用并回放结果的 API 客户端替身。"""

    def __init__(self, get_result=None, post_result=None):
        self.calls = []
        self._get = get_result if get_result is not None else {}
        self._post = post_result if post_result is not None else {"success": True}

    def get(self, path):
        self.calls.append(("GET", path, None))
        return self._get

    def post(self, path, payload=None):
        self.calls.append(("POST", path, payload))
        return self._post


def _sargs(**kwargs):
    base = dict(backend_port=None, service=None, managed=False, json_=False,
                quiet=False, timeout=30, command="serial",
                text=None, hex=None, file=None, no_newline=False,
                no_rts=False, no_dtr=False, rts=False, dtr=False, baud=None,
                out=None, state=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_serial_ports_and_open():
    print("[8] serial ports / open")
    from omni_cli.commands import serial as S

    c = StubClient(get_result={"ports": [
        {"device": "COM3", "description": "USB-SERIAL CH340",
         "vid": "0x1A86", "pid": "0x7523", "manufacturer": "wch.cn"},
    ]})
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = S._ports(c, _sargs())
    check("ports 退出码 0", rc == 0)
    check("端口表含 VID:PID", "COM3" in out.text and "0x1A86:0x7523" in out.text, out.text)

    # open 的请求载荷必须与后端 SerialOpenRequest 对齐
    c = StubClient(post_result={"success": True})
    args = _sargs(port="COM3", baud=921600, bytesize=8, parity="E",
                   stopbits=2, flow="rtscts", rts=True, dtr=False, id="ch340")
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = S._open(c, args)
    method, path, payload = c.calls[0]
    check("open 调 POST /api/serial/open", (method, path) == ("POST", "/api/serial/open"))
    check("载荷字段与后端模型对齐",
          payload == {"connection_id": "ch340", "port": "COM3", "baudrate": 921600,
                      "bytesize": 8, "parity": "E", "stopbits": 2,
                      "flow_control": "rtscts", "rts": True, "dtr": False},
          str(payload))
    check("人类可读输出含参数", "COM3 @ 921600" in out.text and "8E2" in out.text, out.text)


def test_serial_send():
    print("[9] serial send 三种来源")
    from omni_cli.commands import serial as S

    def _run(args, c):
        out, err = FakeStdout(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = S._send(c, args)
        return rc, out, err

    # 文本默认补换行
    c = StubClient()
    rc, out, _ = _run(_sargs(text="AT", id="cli"), c)
    _, path, payload = c.calls[0]
    check("text 换行编码为 hex 且默认补 \\n", payload["data"] == b"AT\n".hex().upper(),
          payload["data"])
    check("发送回显字节数", "已发送 3 字节" in out.text, out.text)

    # --no-newline
    c = StubClient()
    rc, out, _ = _run(_sargs(text="AT", no_newline=True, id="cli"), c)
    _, _, payload = c.calls[0]
    check("--no-newline 不补换行", payload["data"] == b"AT".hex().upper(), payload["data"])

    # hex
    c = StubClient()
    rc, out, err = _run(_sargs(hex="48 65", id="cli"), c)
    _, _, payload = c.calls[0]
    check("hex 允许空格写法", payload["data"] == b"He".hex().upper(), payload["data"])

    # file
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"\x01\x02\x03")
        fname = f.name
    try:
        c = StubClient()
        rc, out, _ = _run(_sargs(file=fname, id="cli"), c)
        _, _, payload = c.calls[0]
        check("file 原始字节发送", payload["data"] == b"\x01\x02\x03".hex().upper())
    finally:
        os.unlink(fname)

    # 多来源/无来源拒绝
    c = StubClient()
    rc, _, err = _run(_sargs(text="a", hex="41"), c)
    check("双来源拒绝且不发请求", rc == 1 and c.calls == [], err.getvalue())
    rc, _, _ = _run(_sargs(), StubClient())
    check("无来源拒绝", rc == 1)

    # 非法 hex
    rc, _, err = _run(_sargs(hex="486"), StubClient())
    check("奇数长度 hex 拒绝", rc == 1 and "偶数" in err.getvalue(), err.getvalue())


def test_serial_read_and_update():
    print("[10] serial read 请求形态 / update 参数组装")
    from omni_cli.commands import serial as S

    # 普通 read：GET /api/serial/data，携带 id 与游标
    c = StubClient(get_result=_resp([_chunk(1, b"OK")], next_seq=2, cursor=2))
    args = _sargs(id="cli", since=0, max_bytes=4096, wait=None, follow=False,
                  interval=0.2, duration=0.0, out=None, encoding=None, hex=False)
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = S._read(c, args)
    method, path, _ = c.calls[0]
    check("read 走 GET /api/serial/data", (method, path.split("?")[0]) == ("GET", "/api/serial/data"))
    check("查询串含连接实例与游标", "connection_id=cli" in path and "since=0" in path, path)
    check("原始字节直写 stdout", out.raw == b"OK", repr(out.raw))

    # --wait：POST /api/serial/read，携带 timeout_ms
    c = StubClient(post_result=_resp([_chunk(1, b"OK")], next_seq=2, cursor=2))
    args = _sargs(id="cli", since=0, max_bytes=4096, wait=500, follow=False,
                  interval=0.2, duration=0.0, out=None, encoding=None, hex=False)
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = S._read(c, args)
    method, path, payload = c.calls[0]
    check("--wait 走 POST /api/serial/read", (method, path) == ("POST", "/api/serial/read"))
    check("--wait 载荷含 timeout_ms", payload["timeout_ms"] == 500, str(payload))

    # --wait 与 --follow 互斥
    c = StubClient()
    args = _sargs(id="cli", since=0, max_bytes=4096, wait=500, follow=True,
                  interval=0.2, duration=0.0, out=None, encoding=None, hex=False)
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = S._read(c, args)
    check("--wait 与 --follow 拒绝", rc == 1 and "不能与 --follow" in err.getvalue(),
          err.getvalue())

    # update：参数组装
    c = StubClient()
    args = _sargs(id="cli", baud=460800, rts=True, no_dtr=True)
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = S._update(c, args)
    _, path, payload = c.calls[0]
    check("update 载荷只含变化的参数",
          payload == {"connection_id": "cli", "baudrate": 460800,
                      "rts": True, "dtr": False}, str(payload))

    rc, _, err = _run_quiet_noop_update()
    check("update 无参数拒绝", rc == 1)


def _run_quiet_noop_update():
    from omni_cli.commands import serial as S
    c = StubClient()
    args = _sargs(id="cli")
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = S._update(c, args)
    return rc, out, err


def test_record_and_auto_send():
    print("[12] record / auto-send 请求组装")
    from omni_cli.commands import serial as S

    c = StubClient()
    rc, out, _ = _run_serial(S._record_start, c,
                             _sargs(id="cli", out="D:\\log\\rx.dat"))
    check("record start 载荷",
          c.calls[0] == ("POST", "/api/serial/record/start",
                         {"connection_id": "cli", "path": "D:\\log\\rx.dat"}),
          str(c.calls[0]))
    check("提示后端落盘", "关掉终端仍继续" in out, out)

    c = StubClient(post_result={"success": True, "bytes_written": 4096})
    rc, out, _ = _run_serial(S._record_stop, c, _sargs(id="cli"))
    check("record stop 回显字节数", "4096" in out, out)

    c = StubClient(get_result={"connected": True, "port": "COM3", "baudrate": 115200,
                               "bytesize": 8, "parity": "N", "stopbits": 1,
                               "recording": {"active": True, "path": "D:\\log\\rx.dat",
                                             "bytes": 1024}})
    rc, out, _ = _run_serial(S._record_status, c, _sargs(id="cli"))
    check("record status 显示路径与字节", "rx.dat" in out and "1024" in out, out)

    c = StubClient(get_result={"connected": True, "recording": {"active": False}})
    rc, out, _ = _run_serial(S._record_status, c, _sargs(id="cli"))
    check("未录制时退出码 1", rc == 1, str(rc))

    c = StubClient()
    rc, out, _ = _run_serial(S._auto_send_start, c,
                             _sargs(id="cli", hex="AA BB", interval=500))
    check("auto-send start 载荷",
          c.calls[0] == ("POST", "/api/serial/auto-send/start",
                         {"connection_id": "cli", "data": "AABB", "interval_ms": 500}),
          str(c.calls[0]))
    check("提示后端持有定时器", "后端持有定时器" in out, out)

    c = StubClient(post_result={"success": True, "sent": 20, "errors": 0})
    rc, out, _ = _run_serial(S._auto_send_stop, c, _sargs(id="cli"))
    check("auto-send stop 回显计数", "发送 20 次" in out, out)

    c = StubClient(post_result={"success": True, "interval_ms": 200, "bytes": 1})
    rc, out, _ = _run_serial(S._auto_send_update, c,
                             _sargs(id="cli", hex="01", interval=200))
    check("auto-send update 载荷",
          c.calls[0][2] == {"connection_id": "cli", "data": "01", "interval_ms": 200},
          str(c.calls[0][2]))

    c = StubClient()
    rc, out, err = _run_serial(S._auto_send_update, c, _sargs(id="cli", hex=None, interval=None))
    check("update 无参数拒绝", rc == 1 and "没有要更新" in err, err)


def _run_serial(func, c, args):
    out, err = FakeStdout(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = func(c, args)
    return rc, out.text, err.getvalue()


def test_categorized_help():
    print("[11] 分类帮助覆盖全部命令")
    from omni_cli.cli import COMMAND_CATEGORIES, build_parser, _COMMAND_INDENT, _grouped_commands
    parser = build_parser()
    sub_action = next(a for a in parser._actions
                      if isinstance(a, argparse_SubParsersAction()))
    names = {c.dest for c in sub_action._choices_actions}
    listed = set()
    for _, members in COMMAND_CATEGORIES:
        listed |= set(members)
    check("所有命令都归入类别", names == listed, f"未分类: {sorted(names - listed)} 类别多余: {sorted(listed - names)}")
    grouped = _grouped_commands(sub_action._choices_actions)
    grouped_names = {c.dest for c in grouped if c.help}
    check("分组结果不丢命令", grouped_names == names,
          f"丢失: {sorted(names - grouped_names)}")
    headers = [c.dest for c in grouped if not c.help]
    check("类别标题在前且有序", headers == [t for t, ms in COMMAND_CATEGORIES if set(ms) & names],
          str(headers))

    # 渲染层：分组标题逐个出现，命令按类别落在标题之后，options 段收尾。
    text = parser.format_help()
    check("帮助渲染出全部类别标题", all(t in text for t in headers),
          "缺失: %s" % [t for t in headers if t not in text])
    name_w = max(len(c.dest) for c in grouped if c.help)
    positions = [text.index(_COMMAND_INDENT + c.dest.ljust(name_w))
                 for c in grouped if c.help]
    check("命令顺序与分组一致", positions == sorted(positions))
    check("命令块位于 options 段之前",
          text.index(headers[0]) < text.index("options:"))
    check("帮助不再输出 positionals 噪音", "positional arguments" not in text)
    check("选项段仍完整", "--version" in text and "--help" in text)


def argparse_SubParsersAction():
    import argparse
    return argparse._SubParsersAction


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass
    test_raw_mode()
    test_decode_mode()
    test_hex_mode()
    test_json_mode()
    test_follow_and_cursor()
    test_warnings_and_errors()
    test_out_file()
    test_serial_ports_and_open()
    test_serial_send()
    test_serial_read_and_update()
    test_record_and_auto_send()
    test_categorized_help()
    print()
    if failures:
        print(f"FAILED: {len(failures)} 项")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
