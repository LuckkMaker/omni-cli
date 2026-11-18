"""net 命令组离线测试（无需硬件、无需后端）。

覆盖：

1. 请求载荷组装：tcp-server start/send/kick/echo、tcp-client、udp
2. 接收读取：GET /api/network/data 的查询串（kind/since/peer）、
   POST /api/network/read 的 timeout_ms
3. ping：结果渲染与丢包判定（有回包退出码 0，全丢包退出码 1）
4. 帮助分类：net 归入「串口与网络」类

用法::

    python tests/test_net_cli.py
"""

import contextlib
import io
import json
import os
import sys
from types import SimpleNamespace

PY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PY_DIR)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures.append(name)
    print(f"  {status}  {name}" + (f"  ({detail})" if detail and not cond else ""))


class StubClient:
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


def _args(**kwargs):
    base = dict(backend_port=None, service=None, managed=False, json_=False,
                quiet=False, timeout=30, command="net",
                text=None, hex=None, file=None, no_newline=False,
                target=None, peer=None, kind="tcp-client",
                host=None, count=4, timeout_ms=1000, size=32, last=False,
                root=None, remote=None, local=None, max_wait=300,
                since=0, max_bytes=65536, wait=None, follow=False,
                interval=0.2, duration=0.0, out=None, encoding=None,
                hex_=False)
    base.update(kwargs)
    return SimpleNamespace(**base)


def _run(func, c, args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = func(c, args)
    return rc, out.getvalue(), err.getvalue()


def test_tcp_server_requests():
    print("[1] tcp-server 请求组装")
    from omni_cli.commands import net as N

    c = StubClient()
    rc, out, _ = _run(N._ts_start, c, _args(host="0.0.0.0", port=6000))
    check("start 载荷", c.calls[0] == ("POST", "/api/network/tcp-server/start",
                                      {"host": "0.0.0.0", "port": 6000}), str(c.calls[0]))
    check("start 回显", "0.0.0.0:6000" in out, out)

    c = StubClient()
    rc, out, _ = _run(N._ts_send, c, _args(text="hello", target=["c1", "c2"]))
    _, path, payload = c.calls[0]
    check("send 目标列表透传", payload["targets"] == ["c1", "c2"], str(payload))
    check("send 默认补换行", payload["data"] == b"hello\n".hex().upper(), payload["data"])
    check("send 回显目标数", "2 个客户端" in out, out)

    c = StubClient()
    rc, out, _ = _run(N._ts_send, c, _args(hex="0A0B"))
    check("send 无 target 时广播（None）", c.calls[0][2]["targets"] is None,
          str(c.calls[0][2]))
    check("广播文案", "全部客户端" in out, out)

    c = StubClient()
    rc, out, _ = _run(N._ts_kick, c, _args(id="c1"))
    check("kick 载荷", c.calls[0][2] == {"client_id": "c1"}, str(c.calls[0][2]))

    c = StubClient()
    rc, out, _ = _run(N._ts_echo, c, _args(state="on"))
    check("echo on", c.calls[0][2] == {"enabled": True}, str(c.calls[0][2]))
    c = StubClient()
    rc, out, _ = _run(N._ts_echo, c, _args(state="off"))
    check("echo off", c.calls[0][2] == {"enabled": False}, str(c.calls[0][2]))


def test_tcp_client_requests():
    print("[2] tcp-client 请求组装")
    from omni_cli.commands import net as N

    c = StubClient()
    rc, out, _ = _run(N._tc_connect, c, _args(host="192.168.1.100", port=6000))
    check("connect 载荷", c.calls[0] == ("POST", "/api/network/tcp-client/connect",
                                        {"host": "192.168.1.100", "port": 6000}),
          str(c.calls[0]))
    check("connect 回显", "192.168.1.100:6000" in out, out)

    c = StubClient()
    rc, out, _ = _run(N._tc_send, c, _args(text="ping", no_newline=True))
    check("send 不补换行", c.calls[0][2]["data"] == b"ping".hex().upper(),
          c.calls[0][2]["data"])

    c = StubClient(get_result={"running": True, "host": "1.2.3.4", "port": 80,
                               "rx": 100, "tx": 50,
                               "capture": {"buffered_bytes": 100, "oldest_seq": 1,
                                           "next_seq": 2}})
    rc, out, _ = _run(N._tc_status, c, _args())
    check("status 显示收发字节", "接收 100 字节" in out and "发送 50 字节" in out, out)
    check("status 显示缓冲", "接收缓冲: 100 字节" in out, out)


def test_udp_requests():
    print("[3] udp 请求组装")
    from omni_cli.commands import net as N

    c = StubClient()
    rc, out, _ = _run(N._udp_start, c, _args(host="0.0.0.0", port=5000))
    check("udp start 载荷", c.calls[0] == ("POST", "/api/network/udp/start",
                                          {"local_host": "0.0.0.0", "local_port": 5000}),
          str(c.calls[0]))

    c = StubClient()
    rc, out, _ = _run(N._udp_send, c, _args(to="192.168.1.255", port=5000, hex="FF"))
    check("udp send 载荷", c.calls[0] == ("POST", "/api/network/udp/send",
                                         {"data": "FF", "target_host": "192.168.1.255",
                                          "target_port": 5000}), str(c.calls[0]))
    check("udp send 回显目标", "192.168.1.255:5000" in out, out)


def test_read_requests():
    print("[4] read 请求形态")
    from omni_cli.commands import net as N

    # GET /api/network/data：kind/since/max_bytes/peer
    c = StubClient(get_result={"success": True, "chunks": [], "cursor": 1,
                               "next_seq": 1, "oldest_seq": 1, "lost": False,
                               "truncated": False, "session_reset": False,
                               "last_seq": 0, "dropped_bytes": 0, "dropped_chunks": 0})
    args = _args(kind="tcp-server", since=7, peer="192.168.1.101:52036")
    rc, out, err = _run(N._read, c, args)
    method, path, _ = c.calls[0]
    check("read 走 GET /api/network/data", (method, path.split("?")[0]) ==
          ("GET", "/api/network/data"), str(c.calls[0]))
    check("查询串含 kind", "kind=tcp-server" in path, path)
    check("查询串含 since", "since=7" in path, path)
    check("查询串含 peer", "peer=192.168.1.101:52036" in path, path)
    check("空数据给提示", "暂无接收数据" in err, err)

    # POST /api/network/read：timeout_ms
    c = StubClient(post_result={"success": True, "chunks": [], "cursor": 1,
                                "next_seq": 1, "oldest_seq": 1, "lost": False,
                                "truncated": False, "session_reset": False,
                                "last_seq": 0, "dropped_bytes": 0, "dropped_chunks": 0})
    args = _args(kind="udp", wait=300)
    rc, out, err = _run(N._read, c, args)
    check("--wait 走 POST /api/network/read", c.calls[0][0] == "POST", str(c.calls[0]))
    check("--wait 载荷含 kind 与 timeout_ms",
          c.calls[0][2]["kind"] == "udp" and c.calls[0][2]["timeout_ms"] == 300,
          str(c.calls[0][2]))

    # --wait 与 --follow 互斥
    c = StubClient()
    rc, out, err = _run(N._read, c, _args(wait=100, follow=True))
    check("--wait 与 --follow 拒绝", rc == 1 and "不能与 --follow" in err, err)


def test_ping():
    print("[5] ping 结果渲染")
    from omni_cli.commands import net as N

    ok = {"host": "192.168.1.100", "sent": 4, "received": 4, "lost": 0,
          "lossPct": 0.0, "min": 0.5, "avg": 1.2, "max": 3.0}
    c = StubClient(get_result={"running": False, "last": ok})
    rc, out, _ = _run(N._ping, c, _args(last=True, json_=False))
    check("--last 直接查最近结果", rc == 0 and "发送 4" in out and "丢包 0" in out, out)
    check("--last 不发新 ping", all(call[0] == "GET" for call in c.calls), str(c.calls))

    all_lost = {"host": "10.0.0.9", "sent": 4, "received": 0, "lost": 4,
                "lossPct": 100.0, "min": None, "avg": None, "max": None}
    c = StubClient(get_result={"running": False, "last": all_lost})
    rc, out, _ = _run(N._ping, c, _args(last=True))
    check("全丢包退出码 1", rc == 1, str(rc))
    check("全丢包不打印时延行", "时延" not in out, out)

    c = StubClient(get_result={"running": False, "last": None})
    rc, out, err = _run(N._ping, c, _args(last=True))
    check("无记录时提示先 ping", rc == 1 and "还没有 ping 记录" in err, err)

    # --json 原样输出
    c = StubClient(get_result={"running": False, "last": ok})
    rc, out, _ = _run(N._ping, c, _args(last=True, json_=True))
    payload = json.loads(out)
    check("--json 输出完整结果", payload["received"] == 4 and payload["lossPct"] == 0.0)


def test_payload_helper():
    print("[6] 载荷归一（三选一）")
    from omni_cli.commands import net as N

    data, err = N._payload(_args(text="ab"))
    check("text 默认补换行", data == b"ab\n" and err is None, repr(data))
    data, err = N._payload(_args(text="ab", no_newline=True))
    check("text 不补换行", data == b"ab", repr(data))
    data, err = N._payload(_args(hex="0A 0B"))
    check("hex 允许空格与 0x", data == b"\x0a\x0b", repr(data))
    data, err = N._payload(_args(hex="0A0"))
    check("奇数长度 hex 报错", data is None and err == 1)
    data, err = N._payload(_args(text="a", hex="41"))
    check("多来源报错", data is None and err == 1)
    data, err = N._payload(_args())
    check("无来源报错", data is None and err == 1)


def test_tftp():
    print("[8] tftp 请求组装")
    from omni_cli.commands import net as N

    c = StubClient()
    rc, out, _ = _run(N._tftp_server_start, c,
                      _args(root="D:\\share", host="0.0.0.0", port=69))
    check("server-start 载荷",
          c.calls[0] == ("POST", "/api/network/tftp/server/start",
                         {"root": "D:\\share", "host": "0.0.0.0", "port": 69}),
          str(c.calls[0]))
    check("server-start 回显", "TFTP 服务器已启动" in out, out)

    c = StubClient()
    rc, out, _ = _run(N._tftp_server_stop, c, _args())
    check("server-stop 走 POST", c.calls[0][0] == "POST" and "tftp/server/stop" in c.calls[0][1])

    c = StubClient(get_result={"running": True, "host": "0.0.0.0", "port": 69,
                               "root": "D:\\share", "activeTransfers": 1})
    rc, out, _ = _run(N._tftp_server_status, c, _args())
    check("server-status 显示根目录与传输数", "D:\\share" in out and "进行中传输 1" in out, out)

    c = StubClient(get_result={"files": [{"name": "fw.bin", "size": 1234}]})
    rc, out, _ = _run(N._tftp_server_files, c, _args())
    check("server-files 表格含文件名与大小", "fw.bin" in out and "1234" in out, out)

    c = StubClient(post_result={"success": True},
                   get_result={"busy": False,
                               "active": {"status": "done", "transferred": 2048,
                                          "total": 2048}})
    rc, out, _ = _run(N._tftp_get, c,
                      _args(host="192.168.1.100", port=69, remote="fw.bin",
                            local="D:\\tmp\\fw.bin", max_wait=60))
    check("get 载荷",
          c.calls[0] == ("POST", "/api/network/tftp/client/download",
                         {"host": "192.168.1.100", "port": 69,
                          "remote": "fw.bin", "local": "D:\\tmp\\fw.bin"}),
          str(c.calls[0]))
    check("get 回显方向与字节数", "已下载" in out and "2048" in out, out)

    c = StubClient(post_result={"success": True},
                   get_result={"busy": False,
                               "active": {"status": "done", "transferred": 512,
                                          "total": 512}})
    rc, out, _ = _run(N._tftp_put, c,
                      _args(host="192.168.1.100", port=69, remote="cfg.ini",
                            local="D:\\tmp\\cfg.ini"))
    check("put 走 upload", "tftp/client/upload" in c.calls[0][1], str(c.calls[0]))
    check("put 回显方向", "已上传" in out, out)

    c = StubClient(post_result={"success": False, "error": "文件不存在"})
    rc, out, err = _run(N._tftp_get, c,
                        _args(host="1.2.3.4", port=69, remote="x", local="y"))
    check("传输失败退出码 1 并透传错误", rc == 1 and "文件不存在" in err, err)

    c = StubClient(post_result={"success": True},
                   get_result={"busy": False,
                               "active": {"status": "error", "error": "超时未收到 DATA"}})
    rc, out, err = _run(N._tftp_get, c,
                        _args(host="1.2.3.4", port=69, remote="x", local="y", max_wait=5))
    check("后台传输报错透传", rc == 1 and "超时未收到 DATA" in err, err)


def test_auto_send():
    print("[9] auto-send 请求组装")
    from omni_cli.commands import net as N

    c = StubClient()
    rc, out, _ = _run(N._ts_auto_start, c,
                      _args(hex="AA BB", interval=200, target=["c1"]))
    check("tcp-server auto-send 载荷",
          c.calls[0][2] == {"data": "AABB", "interval_ms": 200, "targets": ["c1"]},
          str(c.calls[0][2]))
    check("提示后端持有定时器", "后端持有定时器" in out, out)

    c = StubClient()
    rc, out, _ = _run(N._tc_auto_start, c, _args(hex="01", interval=500))
    check("tcp-client auto-send 路径",
          c.calls[0][1] == "/api/network/tcp-client/auto-send/start", str(c.calls[0]))

    c = StubClient()
    rc, out, _ = _run(N._udp_auto_start, c,
                      _args(hex="02", interval=100, to="192.168.1.255", port=5000))
    check("udp auto-send 目标拼成 host:port",
          c.calls[0][2]["targets"] == ["192.168.1.255:5000"], str(c.calls[0][2]))

    c = StubClient(post_result={"success": True, "sent": 42, "errors": 1})
    rc, out, _ = _run(N._ts_auto_stop, c, _args())
    check("stop 回显发送/失败计数", "发送 42 次" in out and "失败 1 次" in out, out)

    c = StubClient()
    rc, out, err = _run(N._tc_auto_start, c, _args(hex="0A0", interval=100))
    check("奇数长度 hex 拒绝", rc == 1 and c.calls == [], err)

    c = StubClient(post_result={"success": False, "error": "TCP Client 未连接"})
    rc, out, err = _run(N._tc_auto_start, c, _args(hex="01", interval=100))
    check("后端拒绝时透传错误", rc == 1 and "未连接" in err, err)


def test_help_category():
    print("[7] 分类帮助包含 net")
    from omni_cli.cli import COMMAND_CATEGORIES
    bus_group = next(m for t, m in COMMAND_CATEGORIES if "串口与网络" in t)
    check("net 与 serial 同属总线类", "net" in bus_group and "serial" in bus_group,
          str(bus_group))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass
    test_tcp_server_requests()
    test_tcp_client_requests()
    test_udp_requests()
    test_read_requests()
    test_ping()
    test_payload_helper()
    test_tftp()
    test_auto_send()
    test_help_category()
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
