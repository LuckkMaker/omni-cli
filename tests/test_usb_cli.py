"""usb 命令组离线测试（无需硬件、无需后端）。

覆盖：

1. 命令面注册：``omni usb --help`` 列出各子命令，``omni --help`` 归入 bus 分组；
2. 未实现处理器：M2/M3/M4 的叶子命令给出退出码 1 与里程碑提示，且不连接后端；
3. 抓包处理器（M1）：用 StubClient 断言请求载荷与渲染，不依赖真实后端；
4. 服务路由：``usb`` 命令组解析到 bus 服务。

用法::

    python tests/test_usb_cli.py
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

PY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PY_DIR)

from omni_cli import cli, runtime  # noqa: E402
from omni_cli.commands import usb as usb_cmd  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures.append(name)
    print(f"  {status}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse 的 --help / 用法错误走 SystemExit
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def ns(**kw) -> argparse.Namespace:
    defaults = dict(
        json_=False,
        quiet=False,
        timeout=30,
        managed=False,
        backend_port=None,
        service=None,
        interval=0.05,
        duration=0.0,
        since=None,
        follow=False,
        limit=200,
        type=None,
        dir=None,
        bus=None,
        device=None,
        endpoint=None,
        status=None,
        q=None,
        transfer_id=1,
        pcap="sample.pcap",
        no_wait=False,
        out=None,
        usbpcap_path=None,
        snaplen=None,
        bufferlen=None,
        devices=None,
        hex=None,
        stdin=False,
        file=None,
        format="hex",
        name="winusb",
        junit=None,
        keep_going=False,
        port=None,
        baud=115200,
        bytesize=8,
        parity="N",
        stopbits=1,
        flow="none",
        rts=False,
        dtr=False,
        text=None,
        no_newline=False,
        duration_ms=250,
        id="cli",
        max_bytes=65536,
        wait=None,
        encoding=None,
        vid=None,
        pid=None,
        serial=None,
        path=None,
        timeout_ms=1000,
        length=None,
        count=1,
        data=None,
        report_id=0,
        get=False,
        set=False,
        guid=None,
        index=None,
        interface=0,
        request_type=None,
        direction="in",
        req_type="standard",
        recipient="device",
        request=None,
        value="0",
        pipe=None,
        repeat=1,
        out_pipe=None,
        in_pipe=None,
        size=64,
        pipeline=1,
        pattern="increment",
        seed=0,
        abort=False,
        flush=False,
        reset=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class StubClient:
    """按路径前缀返回预置响应；记录调用用于断言载荷。"""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, str, dict | None]] = []

    def _match(self, path: str):
        for prefix, value in self.responses.items():
            if path.startswith(prefix):
                return value(path) if callable(value) else value
        raise AssertionError(f"未预置的路径：{path}")

    def get(self, path: str):
        self.calls.append(("GET", path, None))
        return self._match(path)

    def post(self, path: str, payload=None):
        self.calls.append(("POST", path, payload))
        return self._match(path)


ENV_OK = {
    "supported": True,
    "platform": "win32",
    "installed": True,
    "path": r"D:\Program Files\USBPcap\USBPcapCMD.exe",
    "version": None,
    "elevated": True,
    "devices": [{"id": r"\\.\USBPcap1", "label": "USBPcap1", "attached": []}],
    "warnings": [],
}

ENV_MULTI = dict(
    ENV_OK,
    devices=[
        {"id": r"\\.\USBPcap1", "label": "USBPcap1", "attached": []},
        {"id": r"\\.\USBPcap2", "label": "USBPcap2", "attached": []},
    ],
)


def capture(func, client, args) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = func(client, args)
    return code, out.getvalue(), err.getvalue()


def main() -> int:
    # ── 1. 命令面 ────────────────────────────────────────
    code, out, _ = run_cli(["usb", "--help"])
    check("usb --help 退出码 0", code == 0, str(code))
    for sub in ("doctor", "desc", "winusb", "hid", "cdc", "capture", "test"):
        check(f"usb --help 含 {sub}", sub in out)

    code, out, _ = run_cli([])
    check("omni 无子命令返回 2", code == 2, str(code))
    check("omni --help 归入 USB 分组", "USB" in out and "usb" in out)

    code, _, _ = run_cli(["usb"])
    check("usb 无子命令返回 2", code == 2, str(code))

    # ── 2. M3 请求按传输通道拆分（winusb/hid/cdc） ───────

    # ── 2b. desc（M2） ───────────────────────────────────
    doc = {
        "version": 1,
        "device": {
            "bcdUSB": "0x0210",
            "class": 0,
            "subClass": 0,
            "protocol": 0,
            "maxPacketSize0": 64,
            "vid": "0xC251",
            "pid": "0x0002",
            "bcdDevice": "0x0100",
        },
        "configurations": [
            {
                "value": 1,
                "attributes": "0x80",
                "maxPower": 100,
                "interfaces": [
                    {
                        "number": 0,
                        "alternates": [
                            {
                                "alternate": 0,
                                "class": "0xFF",
                                "subClass": 0,
                                "protocol": 0,
                                "endpoints": [
                                    {
                                        "address": "0x01",
                                        "attributes": "0x02",
                                        "maxPacketSize": 64,
                                        "interval": 0,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    setup_item = {
        "success": True,
        "kind": "setup",
        "length": 8,
        "hex": "80 06 00 01 00 00 12 00",
        "setup": {
            "requestName": "GET_DESCRIPTOR",
            "direction": "in",
            "type": "standard",
            "recipient": "device",
            "wValue": 0x0100,
            "wIndex": 0,
            "wLength": 18,
            "decoded": {"descriptorType": "DEVICE"},
        },
    }
    client = StubClient({"/api/usb/desc/parse": setup_item})
    code, out, _ = capture(
        usb_cmd._desc_parse, client, ns(hex="80 06 00 01 00 00 12 00")
    )
    check("desc parse setup", code == 0 and "GET_DESCRIPTOR" in out, out)
    payload = [call for call in client.calls if call[0] == "POST"][0][2]
    check("desc parse 发送 hex", payload.get("hex") == "80 06 00 01 00 00 12 00", str(payload))

    device_item = {"success": True, "kind": "device", "length": 18, "document": doc}
    code, out, _ = capture(
        usb_cmd._desc_parse, StubClient({"/api/usb/desc/parse": device_item}), ns(hex="12 01")
    )
    check("desc parse device", code == 0 and "0xC251" in out and "配置 1" in out, out)

    with tempfile.TemporaryDirectory() as tmp:
        doc_path = Path(tmp) / "doc.json"
        doc_path.write_text(json.dumps(doc), encoding="utf-8")

        code, out, _ = capture(
            usb_cmd._desc_validate,
            StubClient(
                {"/api/usb/desc/validate": {"success": True, "ok": True, "errors": [], "warnings": []}}
            ),
            ns(file=str(doc_path)),
        )
        check("desc validate 通过", code == 0 and "校验通过" in out, out)

        code, out, _ = capture(
            usb_cmd._desc_validate,
            StubClient(
                {
                    "/api/usb/desc/validate": {
                        "success": True,
                        "ok": False,
                        "errors": [{"code": "endpoint.maxPacketSize", "path": "x", "message": "太大"}],
                        "warnings": [],
                    }
                }
            ),
            ns(file=str(doc_path)),
        )
        check("desc validate 失败退出码 1", code == 1 and "endpoint.maxPacketSize" in out, out)

        built = {
            "success": True,
            "device": "12 01 00 02 00 00 00 40",
            "configurations": [{"value": 1, "totalLength": 32, "hex": "09 02 20 00"}],
            "strings": [{"index": 1, "langId": "0x0409", "hex": "0A 03"}],
            "bos": None,
            "validation": {"ok": True, "errors": [], "warnings": []},
        }
        code, out, _ = capture(
            usb_cmd._desc_build, StubClient({"/api/usb/desc/build": built}), ns(file=str(doc_path))
        )
        check("desc build 输出分节", code == 0 and "配置 1 (32 B)" in out, out)

        code, out, _ = capture(
            usb_cmd._desc_emit,
            StubClient({"/api/usb/desc/build": built}),
            ns(file=str(doc_path), format="hex"),
        )
        check(
            "desc emit hex 只输出十六进制",
            code == 0 and "12 01 00 02" in out and "配置" not in out,
            out,
        )

    code, out, _ = capture(
        usb_cmd._desc_template_list,
        StubClient({"/api/usb/desc/templates": {"templates": [{"name": "winusb", "description": "单接口"}]}}),
        ns(),
    )
    check("desc template list", code == 0 and "winusb" in out, out)

    code, out, _ = capture(
        usb_cmd._desc_template_show,
        StubClient({"/api/usb/desc/templates/winusb": {"success": True, "name": "winusb", "document": doc}}),
        ns(name="winusb"),
    )
    check("desc template show", code == 0 and "配置 1" in out, out)

    code, out, _ = capture(
        usb_cmd._desc_from_capture,
        StubClient({"/api/usb/desc/from-capture": {"success": True, "documents": []}}),
        ns(pcap="capture.pcap"),
    )
    check("desc from-capture 空结果给提示", code == 0 and "没有完整" in out, out)

    code, out, _ = capture(
        usb_cmd._desc_from_capture,
        StubClient(
            {
                "/api/usb/desc/from-capture": {
                    "success": True,
                    "documents": [{"bus": 1, "device": 2, "document": doc}],
                }
            }
        ),
        ns(pcap="capture.pcap"),
    )
    check(
        "desc from-capture 渲染设备",
        code == 0 and "bus 1 device 2" in out and "0xC251" in out,
        out,
    )

    # ── 2c. test（M4a：离线 pcap 用例） ──────────────────
    with tempfile.TemporaryDirectory() as tmp:
        case_path = Path(tmp) / "case.json"
        case_path.write_text(
            json.dumps(
                {
                    "name": "demo",
                    "pcap": "enum.pcap",
                    "steps": [{"expect": {"transfers": {"atLeast": 1}}}],
                }
            ),
            encoding="utf-8",
        )
        report = {
            "success": True,
            "name": "demo",
            "ok": True,
            "durationMs": 5,
            "source": {"transfers": 3, "devices": 1},
            "summary": {"steps": 1, "passed": 1, "failed": 0},
            "steps": [
                {
                    "index": 0,
                    "name": "有传输",
                    "action": "expect",
                    "ok": True,
                    "durationMs": 1,
                    "detail": "transfers: 3 条传输",
                }
            ],
            "junit": '<testsuite name="demo"/>',
        }
        junit_path = Path(tmp) / "report.xml"
        client = StubClient({"/api/usb/test/run": report})
        code, out, _ = capture(
            usb_cmd._test_run, client, ns(file=str(case_path), junit=str(junit_path))
        )
        check("test run 通过退出码 0", code == 0 and "PASS" in out, out)
        payload = client.calls[0][2]
        check(
            "test run 相对 pcap 转绝对路径",
            Path(payload["case"]["pcap"]).is_absolute(),
            str(payload["case"]["pcap"]),
        )
        check("test run 默认 fail-fast", payload.get("stopOnFailure") is True, str(payload))
        check(
            "test run 写 JUnit",
            junit_path.is_file() and "testsuite" in junit_path.read_text(encoding="utf-8"),
        )

        failing = dict(
            report,
            ok=False,
            summary={"steps": 1, "passed": 0, "failed": 1},
            steps=[
                {
                    "index": 0,
                    "name": "有传输",
                    "action": "expect",
                    "ok": False,
                    "durationMs": 1,
                    "detail": "期望至少 99 条",
                }
            ],
        )
        code, out, _ = capture(
            usb_cmd._test_run,
            StubClient({"/api/usb/test/run": failing}),
            ns(file=str(case_path)),
        )
        check("test run 失败退出码 1", code == 1 and "FAIL" in out, out)

    code, out, _ = capture(
        usb_cmd._test_list,
        StubClient({"/api/usb/test/schema": {"example": {"name": "demo", "steps": []}}}),
        ns(),
    )
    check("test list 显示用例格式", code == 0 and "demo" in out, out)

    # ── 2e. cdc（复用串口后端） ──────────────────────────
    ports = {
        "ports": [
            {
                "device": "COM65",
                "description": "USB 串行设备",
                "vid": "0xC251",
                "pid": "0x0001",
                "manufacturer": "Geehy",
            },
            {"device": "COM1", "description": "通信端口", "vid": None, "pid": None},
        ]
    }
    code, out, _ = capture(
        usb_cmd._cdc_list, StubClient({"/api/serial/ports": ports}), ns()
    )
    check("cdc list 只列 USB 设备", code == 0 and "COM65" in out and "COM1" not in out, out)

    client = StubClient({"/api/serial/open": {"success": True}})
    code, out, _ = capture(usb_cmd._cdc_open, client, ns(port="COM65"))
    check("cdc open 退出码 0", code == 0 and "COM65" in out, out)
    payload = client.calls[0][2]
    check(
        "cdc open 载荷正确",
        payload.get("port") == "COM65" and payload.get("connection_id") == "cli",
        str(payload),
    )

    client = StubClient({"/api/serial/break": {"success": True}})
    code, out, _ = capture(usb_cmd._cdc_break, client, ns(duration_ms=100))
    check("cdc break 退出码 0", code == 0 and "BREAK" in out, out)
    check(
        "cdc break 载荷含 duration_ms",
        client.calls[0][2].get("duration_ms") == 100,
        str(client.calls[0][2]),
    )

    code, _, _ = capture(
        usb_cmd._cdc_status,
        StubClient({"/api/serial/status": {"connected": False}}),
        ns(),
    )
    check("cdc status 未打开退出码 1", code == 1, str(code))

    # ── 2f. hid（系统 HID 驱动） ─────────────────────────
    hid_devices = {
        "success": True,
        "devices": [
            {
                "vid": "0xC251",
                "pid": "0x0001",
                "product": "HID Demo",
                "manufacturer": "Geehy",
                "serial": "1",
                "inputReportLength": 64,
                "outputReportLength": 64,
                "featureReportLength": 0,
            },
            {
                "vid": "0x1234",
                "pid": "0x0001",
                "product": "Other",
                "manufacturer": "-",
                "serial": "",
                "inputReportLength": 8,
                "outputReportLength": 8,
                "featureReportLength": 0,
            },
        ],
    }
    code, out, _ = capture(
        usb_cmd._hid_list, StubClient({"/api/usb/hid/devices": hid_devices}), ns()
    )
    check("hid list 显示设备", code == 0 and "HID Demo" in out and "64" in out, out)

    code, out, _ = capture(
        usb_cmd._hid_list,
        StubClient({"/api/usb/hid/devices": hid_devices}),
        ns(vid="0x1234"),
    )
    check("hid list 按 VID 过滤", "HID Demo" not in out and "Other" in out, out)

    client = StubClient(
        {
            "/api/usb/hid/open": {
                "success": True,
                "device": hid_devices["devices"][0],
                "id": "cli",
            }
        }
    )
    code, out, _ = capture(
        usb_cmd._hid_open, client, ns(vid="0xC251", pid="0x0001")
    )
    check("hid open 退出码 0", code == 0 and "0xC251" in out, out)
    payload = client.calls[0][2]
    check(
        "hid open 载荷 vid/pid 为整数",
        payload.get("vid") == 0xC251 and payload.get("pid") == 0x0001,
        str(payload),
    )

    code, out, _ = capture(
        usb_cmd._hid_read,
        StubClient(
            {
                "/api/usb/hid/read": {
                    "success": True,
                    "dataHex": "01 02 03",
                    "length": 3,
                }
            }
        ),
        ns(),
    )
    check("hid read 显示十六进制", code == 0 and "01 02 03" in out, out)

    client = StubClient({"/api/usb/hid/write": {"success": True, "bytesWritten": 64}})
    code, out, _ = capture(
        usb_cmd._hid_write, client, ns(data="AA BB", report_id=1)
    )
    check("hid write 退出码 0", code == 0 and "64" in out, out)
    payload = client.calls[0][2]
    check(
        "hid write 载荷正确",
        payload.get("hex") == "AABB" and payload.get("report_id") == 1,
        str(payload),
    )

    code, _, _ = capture(
        usb_cmd._hid_status,
        StubClient({"/api/usb/hid/status": {"success": True, "connected": False}}),
        ns(),
    )
    check("hid status 未打开退出码 1", code == 1, str(code))

    # ── 2g. winusb（宿主通道） ───────────────────────────
    winusb_device = {
        "vid": "0xC251",
        "pid": "0x0002",
        "speed": "full",
        "bcdUSB": "0x0210",
        "interfaceNumber": 0,
        "class": "0xFF",
        "path": r"\\?\usb#vid_c251&pid_0002",
        "endpoints": [
            {"address": "0x01", "direction": "out", "type": "bulk", "maxPacketSize": 64, "interval": 0},
            {"address": "0x81", "direction": "in", "type": "bulk", "maxPacketSize": 64, "interval": 0},
        ],
    }
    winusb_list = {"success": True, "devices": [winusb_device]}
    code, out, _ = capture(
        usb_cmd._winusb_list,
        StubClient({"/api/usb/winusb/devices": winusb_list}),
        ns(),
    )
    check("winusb list 显示设备", code == 0 and "0xC251" in out and "2" in out, out)

    code, out, _ = capture(
        usb_cmd._winusb_info,
        StubClient({"/api/usb/winusb/devices": winusb_list}),
        ns(),
    )
    check("winusb info 显示端点", code == 0 and "bulk" in out and "0x81" in out, out)

    client = StubClient(
        {"/api/usb/winusb/open": {"success": True, "device": winusb_device, "id": "cli"}}
    )
    code, out, _ = capture(
        usb_cmd._winusb_open, client, ns(vid="0xC251", pid="0x0002")
    )
    check("winusb open 退出码 0", code == 0 and "0xC251" in out, out)
    payload = client.calls[0][2]
    check(
        "winusb open 载荷 vid/pid",
        payload.get("vid") == 0xC251 and payload.get("pid") == 0x0002,
        str(payload),
    )

    client = StubClient(
        {"/api/usb/winusb/control": {"success": True, "dataHex": "12 01 00", "length": 3}}
    )
    code, out, _ = capture(
        usb_cmd._winusb_control, client, ns(request="0x06", value="0x0100", length="3")
    )
    check("winusb control 退出码 0", code == 0 and "12 01 00" in out, out)
    payload = client.calls[0][2]
    check(
        "winusb control 默认 bmRequestType=0x80",
        payload.get("request_type") == 0x80,
        str(payload.get("request_type")),
    )

    code, out, _ = capture(
        usb_cmd._winusb_read,
        StubClient(
            {"/api/usb/winusb/transfer": {"success": True, "dataHex": "AA BB", "length": 2}}
        ),
        ns(pipe="0x81", length=2),
    )
    check("winusb read 显示数据", code == 0 and "AA BB" in out, out)

    client = StubClient(
        {"/api/usb/winusb/transfer": {"success": True, "bytesWritten": 2}}
    )
    code, out, _ = capture(
        usb_cmd._winusb_write, client, ns(pipe="0x01", data="AA BB")
    )
    check("winusb write 退出码 0", code == 0 and "2" in out, out)
    payload = client.calls[0][2]
    check("winusb write 方向为 out", payload.get("direction") == "out", str(payload))

    echo_ok = {
        "success": True,
        "iterations": 2,
        "size": 64,
        "pipeline": 1,
        "throughputKiBs": 45.3,
        "mismatches": 0,
        "firstError": None,
    }
    code, out, _ = capture(
        usb_cmd._winusb_echo,
        StubClient({"/api/usb/winusb/echo": echo_ok}),
        ns(out_pipe="0x01", in_pipe="0x81"),
    )
    check("winusb echo 通过退出 0", code == 0 and "45.3" in out, out)

    echo_bad = dict(echo_ok, mismatches=1, firstError="第 1 次不一致")
    code, _, _ = capture(
        usb_cmd._winusb_echo,
        StubClient({"/api/usb/winusb/echo": echo_bad}),
        ns(out_pipe="0x01", in_pipe="0x81"),
    )
    check("winusb echo 不一致退出 1", code == 1, str(code))

    # ── 3. doctor（M1） ──────────────────────────────────
    code, out, _ = capture(
        usb_cmd._doctor, StubClient({"/api/usb/environment": ENV_OK}), ns()
    )
    check("doctor 已安装退出码 0", code == 0, str(code))
    check("doctor 显示路径", "USBPcapCMD.exe" in out)
    check("doctor 显示管理员状态", "管理员" in out)

    env_missing = dict(ENV_OK, installed=False, path=None, devices=[])
    code, out, _ = capture(
        usb_cmd._doctor, StubClient({"/api/usb/environment": env_missing}), ns()
    )
    check("doctor 未安装仍退出 0", code == 0)
    check("doctor 未安装给安装指引", "desowin.org" in out)

    # ── 4. capture start ─────────────────────────────────
    client = StubClient(
        {
            "/api/usb/environment": ENV_OK,
            "/api/usb/capture/start": {"success": True, "capturePath": "C:/tmp/usb-1.pcap"},
        }
    )
    code, out, _ = capture(usb_cmd._capture_start, client, ns())
    check("start 唯一 hub 自动选择", code == 0 and r"USBPcap1" in out, out)
    payload = [call for call in client.calls if call[0] == "POST"][0][2]
    check("start 载荷含 device_id", payload.get("device_id") == r"\\.\USBPcap1", str(payload))

    client = StubClient({"/api/usb/environment": ENV_MULTI})
    code, _, err = capture(usb_cmd._capture_start, client, ns())
    check("start 多 hub 要求 --device", code == 1 and "--device" in err, err)

    client = StubClient(
        {
            "/api/usb/environment": ENV_MULTI,
            "/api/usb/capture/start": {
                "success": False,
                "code": "not_elevated",
                "error": "USB 抓包需要管理员权限",
            },
        }
    )
    code, _, err = capture(usb_cmd._capture_start, client, ns(device=r"\\.\USBPcap2"))
    check("start 失败给可执行提示", code == 1 and "管理员" in err, err)

    # ── 5. status / transfers / detail / save ────────────
    status = {
        "running": True,
        "loading": False,
        "mode": "live",
        "deviceId": r"\\.\USBPcap1",
        "startedAt": "2026-11-18T12:00:00",
        "capturePath": "C:/tmp/usb-1.pcap",
        "buffered": 3,
        "ringSize": 50000,
        "totals": {"transfers": 3, "bytes": 128, "errors": 0, "unmatched": 0},
    }
    code, out, _ = capture(
        usb_cmd._capture_status, StubClient({"/api/usb/capture/status": status}), ns()
    )
    check("status 退出码 0", code == 0, str(code))
    check("status 显示传输数", "传输" in out and "3" in out)

    transfer = {
        "items": [
            {
                "id": 7,
                "ts": 1.5,
                "bus": 1,
                "device": 2,
                "endpoint": 0x81,
                "direction": "in",
                "typeLabel": "bulk",
                "length": 5,
                "statusName": "SUCCESS",
                "ok": True,
                "summary": "BULK IN 5B",
            }
        ],
        "nextId": 8,
        "oldestId": 7,
        "total": 1,
    }
    code, out, _ = capture(
        usb_cmd._capture_transfers, StubClient({"/api/usb/transfers": transfer}), ns()
    )
    check("transfers 退出码 0", code == 0, str(code))
    check("transfers 表格含 ID 与摘要", "7" in out and "BULK IN 5B" in out, out)

    detail = {
        "success": True,
        "id": 7,
        "typeLabel": "control",
        "direction": "in",
        "endpoint": 0x80,
        "statusName": "SUCCESS",
        "length": 18,
        "ts": 1.5,
        "summary": "GET_DESCRIPTOR(DEVICE)",
        "setupHex": "80 06 00 01 00 00 12 00",
        "dataHex": "12 01 00 02 00 00 00 40",
        "dataLength": 8,
        "dataTruncated": False,
        "decodeTree": [{"label": "USB", "value": "1.2", "children": []}],
    }
    code, out, _ = capture(
        usb_cmd._capture_detail,
        StubClient({"/api/usb/transfers/7": detail}),
        ns(transfer_id=7),
    )
    check("detail 退出码 0", code == 0, str(code))
    check("detail 显示 SETUP 与数据", "SETUP" in out and "12 01 00 02" in out)

    client = StubClient({"/api/usb/capture/save": {"success": True, "path": "C:/tmp/a.pcap"}})
    code, out, _ = capture(usb_cmd._capture_save, client, ns(out="C:/tmp/a.pcap"))
    check("save 退出码 0", code == 0 and "a.pcap" in out, out)

    # ── 6. offline open（--no-wait）与进度 ───────────────
    client = StubClient(
        {"/api/usb/capture/open": {"success": True, "path": "sample.pcap", "total": 1024}}
    )
    code, out, _ = capture(
        usb_cmd._capture_open, client, ns(pcap="sample.pcap", no_wait=True)
    )
    check("open --no-wait 退出码 0", code == 0 and "解析" in out, out)

    status_loading = dict(status, loading=True, progress={"processed": 512, "total": 1024, "percent": 50})
    status_done = dict(status, loading=False, progress=None)
    state = {"count": 0}

    def status_response(_path):
        state["count"] += 1
        return status_loading if state["count"] == 1 else status_done

    client = StubClient(
        {
            "/api/usb/capture/open": {"success": True, "path": "sample.pcap", "total": 1024},
            "/api/usb/capture/status": status_response,
        }
    )
    code, out, _ = capture(usb_cmd._capture_open, client, ns(pcap="sample.pcap"))
    check("open 等待解析完成", code == 0 and "已解析" in out, out)

    # ── 7. follow 使用 afterId 游标 ──────────────────────
    calls: list[str] = []

    def transfers_follow(path: str):
        calls.append(path)
        if "after_id" not in path:
            return {"items": [], "nextId": 42}
        if "after_id=42" in path:
            return {"items": [transfer["items"][0]], "nextId": 43}
        return {"items": [], "nextId": 43}

    client = StubClient({"/api/usb/transfers": transfers_follow})
    code, out, _ = capture(
        usb_cmd._capture_transfers,
        client,
        ns(follow=True, duration=0.15, interval=0.05),
    )
    check("follow 默认从 nextId 开始", code == 0 and any("after_id=42" in p for p in calls), str(calls))
    check("follow 输出新增传输", "BULK IN 5B" in out, out)

    # ── 8. 服务路由与参数 ────────────────────────────────
    code, _, err = run_cli(["usb", "capture", "status", "--managed"])
    check(
        "嵌套 status 拒绝 --managed",
        code == 1 and "不支持 --managed" in err,
        err.strip(),
    )

    args = cli.build_parser().parse_args(["usb", "test", "run", "--file", "case.json", "--json"])
    check("test run --json 可解析", getattr(args, "json_", False) is True)
    check("test run 路由到 bus", runtime.service_for(args) == "bus", runtime.service_for(args))

    args = cli.build_parser().parse_args(
        ["usb", "capture", "transfers", "--follow", "--since", "10"]
    )
    check("transfers --follow/--since 可解析", args.follow is True and args.since == 10)
    check("transfers 路由到 bus", runtime.service_for(args) == "bus")

    if failures:
        print(f"\nFAILED: {len(failures)} 项")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
