"""USB 命令组（bus 服务）：描述符 / 请求 / 抓包 / 测试。

- 描述符：desc parse|validate|build|emit|template|from-capture
- 请求：cdc（复用串口）/ hid（系统 HID 驱动）/ winusb（控制/批量/中断 + echo）
- 抓包：doctor、capture start|stop|status|transfers|detail|devices|open|save
- 测试：test run（离线 pcap 断言 + 真机动作）/ test list

"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from .. import settings
from ..client import ApiClient
from ..formatting import (
    ctx_error,
    display_width,
    print_table,
    render_table,
    table_widths,
)
from ..options import common_parser
from ..runtime import finish, run_command
from ._stream import hexdump, run_cursor_stream
from .serial import _parse_hex

#: 失败码 → 可执行的下一步
_HINTS = {
    "not_elevated": "需要管理员权限：以管理员身份重新打开终端后重试。",
    "usbpcap_not_found": "未找到 USBPcapCMD.exe：安装 USBPcap 并重启，"
    "或用 --usbpcap-path 指定路径。",
    "capture_already_running": "已有捕获在运行：先执行 `omni usb capture stop`。",
    "device_busy": "残留的 USBPcapCMD 可能仍占用 root hub："
    "在任务管理器中以管理员身份结束它，或重启系统。",
    "invalid_bufferlen": "bufferlen 取值范围 4096..134217728。",
    "invalid_devices": "--devices 用逗号分隔设备地址，如 1,2,3。",
    "unsupported_platform": "USB 抓包仅支持 Windows。",
    "hid_unavailable": "HID 访问仅支持 Windows。",
    "hid_not_found": "未找到该 VID/PID 的 HID 设备：先 `omni usb hid list` 确认。",
    "hid_busy": "HID 设备被占用（系统驱动或其他进程独占）：关闭占用程序后重试。",
    "winusb_unavailable": "WinUSB 访问仅支持 Windows。",
    "winusb_not_found": "未找到 WinUSB 接口：确认固件带 MS OS 2.0 描述符或用 INF 绑定，"
    "并用 `omni usb winusb list` 检查。",
    "winusb_open_failed": "WinUSB 接口打开失败：设备可能被 Keil/厂商工具占用，或驱动未绑定。",
    "winusb_transfer_failed": "传输失败：检查端点地址、包长、设备是否处于配置状态。",
    "winusb_invalid": "参数无效：检查 hex 数据与长度。",
}

_TRANSFER_HEADERS = ["ID", "时间", "设备", "端点", "Phase", "长度", "状态", "数据", "描述"]

#: 数据列每行显示的字节数（8 字节 = 23 列，窗口过窄时降到 4）
_DATA_BYTES_PER_LINE = 8


def _failure(data: dict, default: str) -> int:
    """统一处理 ``{success:false, code, error}``：打印错误 + 可执行提示。"""
    code = data.get("code") or ""
    message = data.get("error") or default
    hint = _HINTS.get(code)
    return ctx_error(f"{message}\n  {hint}" if hint else message)


def _ts_text(ts) -> str:
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    moment = datetime.fromtimestamp(value)
    return f"{moment:%H:%M:%S}.{int((value % 1) * 1000):03d}"


def _endpoint_text(address: int) -> str:
    return f"0x{int(address):02X}"


def _data_text(item: dict, limit: int, per_line: int = _DATA_BYTES_PER_LINE) -> str:
    """把一行的 payload 渲染成多行 hex（默认每行 16 字节），超限截断并标注。"""
    text = item.get("dataHex") or ""
    if not text:
        return "-"
    parts = text.split()
    shown = parts[: max(0, limit)]
    if not shown:
        return "…"
    lines = [
        " ".join(shown[start : start + per_line])
        for start in range(0, len(shown), per_line)
    ]
    if len(parts) > len(shown) or item.get("dataTruncated"):
        lines[-1] += " …"
    return "\n".join(lines)


def _setup_text(item: dict, per_line: int) -> str:
    """控制传输 SETUP 的八字节，按每行字节数折行。"""
    parts = (item.get("setupHex") or "").split()[:8]
    if not parts:
        return "-"
    return "\n".join(
        " ".join(parts[start : start + per_line])
        for start in range(0, len(parts), per_line)
    )


def _description(item: dict) -> str:
    """Bus Hound 风格的一行描述：控制传输给请求名，批量/中断只保留类级解码。"""
    text = (item.get("summary") or "").strip()
    kind = item.get("type")
    if kind is None:
        kind = {"control": 2, "bulk": 3, "interrupt": 1, "isochronous": 0}.get(
            str(item.get("typeLabel") or "").lower()
        )
    if kind == 2:
        return text.split(" wLength=")[0]
    if text.startswith(("BULK ", "INTERRUPT ", "ISOCHRONOUS ", "ISO ")):
        return ""
    return text


def _transfer_rows(
    items: list[dict],
    data_bytes: int = 64,
    per_line: int = _DATA_BYTES_PER_LINE,
    show_setup: bool = True,
) -> list[list]:
    """一条 URB 一行；控制传输拆成 CTL（SETUP）+ IN/OUT（数据阶段）两行。"""
    rows: list[list] = []
    for item in items:
        device = f"{item.get('bus')}.{item.get('device')}"
        endpoint = _endpoint_text(item.get("endpoint", 0))
        status = item.get("statusName") or "-"
        desc = _description(item)
        kind = item.get("type")
        if kind is None:
            kind = {"control": 2, "bulk": 3, "interrupt": 1, "isochronous": 0}.get(
                str(item.get("typeLabel") or "").lower()
            )
        direction = str(item.get("direction", "")).upper()

        def base(phase, length, data_text):
            row = [
                item.get("id"),
                _ts_text(item.get("ts")),
                device,
                endpoint,
                phase,
                length,
                status,
            ]
            if data_bytes:
                row.append(data_text)
            row.append(desc)
            return row

        if show_setup and kind == 2 and item.get("setupHex"):
            rows.append(base("CTL", 0, _setup_text(item, per_line) if data_bytes else ""))
            body = _data_text(item, data_bytes, per_line) if data_bytes else ""
            if body and body != "-":
                row = ["", "", device, endpoint, direction or "IN",
                       item.get("length"), ""]
                if data_bytes:
                    row.append(body)
                row.append("")
                rows.append(row)
            continue

        phase = "URB" if kind not in (0, 1, 2, 3) else (direction or "-")
        if kind == 2 and not item.get("length"):
            phase = "CTL"
        body = _data_text(item, data_bytes, per_line) if data_bytes else ""
        rows.append(base(phase, item.get("length"), body))
    return rows


def _transfer_headers(data_bytes: int = 64) -> list[str]:
    if data_bytes:
        return list(_TRANSFER_HEADERS)
    return [header for header in _TRANSFER_HEADERS if header != "数据"]


#: 数据列可选的行宽；`--data-per-line` 指定目标值，放不下时向下降级
_DATA_LINE_CHOICES = (16, 8, 4)

#: follow 模式的固定列宽基数（数据/描述列按窗口宽度再调整）
_FOLLOW_MIN_WIDTHS = {
    "ID": 6,
    "时间": 12,
    "设备": 5,
    "端点": 6,
    "Phase": 6,
    "长度": 6,
    "状态": 10,
    "数据": 49,
    "描述": 34,
}

_DESC_MIN_WIDTH = 16


def _terminal_columns() -> int:
    """终端列数；管道/重定向时用 120 作为稳妥默认。"""
    return max(40, shutil.get_terminal_size((120, 24)).columns)

def _fit_widths(headers: list[str], widths: list[int], columns: int) -> list[int]:
    """超宽时先压摘要列（最多压到 16 列），数据列宽度不动。"""
    widths = list(widths)
    if "描述" not in headers:
        return widths
    index = headers.index("描述")
    total = sum(widths) + 2 * (len(widths) - 1)
    if total <= columns:
        return widths
    widths[index] -= min(total - columns, max(0, widths[index] - _DESC_MIN_WIDTH))
    return widths


def _line_choices(target: int) -> list[int]:
    """目标行宽优先，放不下时只向更窄的档位降级。"""
    return [target] + [width for width in (8, 4) if width < target]


def _transfer_layout(
    items: list[dict],
    data_bytes: int,
    columns: int,
    per_line: int = _DATA_BYTES_PER_LINE,
    show_setup: bool = True,
) -> tuple[list[str], list[list], list[int]]:
    """按窗口宽度选每行字节数，返回 (表头, 行, 列宽)。"""
    headers = _transfer_headers(data_bytes)
    rows: list[list] = []
    widths: list[int] = []
    for line_bytes in _line_choices(per_line):
        rows = _transfer_rows(items, data_bytes, line_bytes, show_setup)
        widths = _fit_widths(headers, table_widths(headers, rows), columns)
        if sum(widths) + 2 * (len(widths) - 1) <= columns:
            return headers, rows, widths
    return headers, rows, widths


def _follow_layout(
    data_bytes: int, columns: int, per_line: int = _DATA_BYTES_PER_LINE
) -> tuple[list[str], int, list[int]]:
    """follow 用固定列宽（跨批次不跳列），同样按窗口宽度降级。"""
    headers = _transfer_headers(data_bytes)
    base = [_FOLLOW_MIN_WIDTHS.get(header, 0) for header in headers]
    fitted: list[int] = []
    for line_bytes in _line_choices(per_line):
        widths = [max(display_width(header), width) for header, width in zip(headers, base)]
        if data_bytes:
            widths[headers.index("数据")] = 3 * line_bytes + 1  # 每字节 3 列 + 截断标记
        fitted = _fit_widths(headers, widths, columns)
        if sum(fitted) + 2 * (len(fitted) - 1) <= columns:
            return headers, line_bytes, fitted
    return headers, _line_choices(per_line)[-1], fitted


def _print_transfers(
    items: list[dict],
    data_bytes: int = 64,
    per_line: int = _DATA_BYTES_PER_LINE,
    show_setup: bool = True,
) -> None:
    headers, rows, widths = _transfer_layout(
        items, data_bytes, _terminal_columns(), per_line, show_setup
    )
    print(render_table(headers, rows, widths=widths))


def _environment_text(env: dict, bound: str | None = None, show_bound: bool = False) -> str:
    lines = []
    platform = env.get("platform") or env.get("supported")
    lines.append(f"平台        : {platform}")
    if not env.get("supported"):
        lines.append("USBPcap     : 不支持（仅 Windows）")
        lines.append("结论        : 抓包不可用；描述符解析等离线能力不受影响")
        return "\n".join(lines)

    installed = bool(env.get("installed"))
    lines.append(f"USBPcap     : {'已安装' if installed else '未安装'}")
    if installed:
        lines.append(f"路径        : {env.get('path') or '-'}")
    lines.append(f"管理员      : {'是' if env.get('elevated') else '否'}")
    devices = env.get("devices") or []
    if devices:
        # 打印设备路径而不是标签：USBPcapCMD 的 -d 只认 \\.\USBPcapN，
        # 短名（USBPcap1）会直接退出且不报错，用户照着提示敲必然抓不到数据。
        labels = ", ".join(str(item.get("id") or item.get("label")) for item in devices)
        lines.append(f"可抓 root hub: {labels}")
    else:
        lines.append("可抓 root hub: 无")
    if show_bound:
        lines.append(
            f"已绑定 hub  : {bound}"
            if bound
            else "已绑定 hub  : 未绑定（capture start 时自动选择唯一的一个）"
        )

    warnings = env.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("警告:")
        for item in warnings:
            lines.append(f"  - {item}")

    lines.append("")
    if not installed:
        lines.append(
            "下一步: 安装 USBPcap（https://desowin.org/usbpcap/），安装后重启系统；"
            "或用 --usbpcap-path 指定 USBPcapCMD.exe。"
        )
    elif not env.get("elevated"):
        lines.append("下一步: 以管理员身份重新打开终端，再执行 capture start。")
    elif not devices:
        lines.append("下一步: 确认 USBPcap 驱动已随安装程序写入（必要时重启）。")
    else:
        lines.append("下一步: omni usb capture start（建议先 start 再插拔目标设备）")
    return "\n".join(lines)


def _doctor(c: ApiClient, args):
    env = c.get("/api/usb/environment")
    bound = settings.usb_capture_hub()
    finish(
        args,
        dict(env, boundHub=bound),
        _environment_text(env, bound, show_bound=True),
    )
    return 0


def _normalize_device(c: ApiClient, wanted: str) -> str:
    r"""把 ``USBPcap1`` 这类短名补成 ``\\.\USBPcap1``。

    USBPcapCMD 的 ``-d`` 只接受设备路径；传短名时它会立刻退出且 stderr 为空，
    表现为「已开始捕获」但一条传输都没有。也接受用户按标签/路径大小写混写。
    """
    text = wanted.strip()
    if text.startswith("\\\\"):
        return text
    try:
        env = c.get("/api/usb/environment")
    except Exception:  # 环境查询失败不该阻断抓包，退回按前缀补全
        env = {}
    for item in env.get("devices") or []:
        if text.lower() in (
            str(item.get("label") or "").lower(),
            str(item.get("id") or "").lower(),
        ):
            return str(item.get("id") or text)
    return "\\\\.\\" + text


def _pick_device(c: ApiClient, wanted: str | None) -> tuple[str | None, int]:
    """选择 root hub：显式指定 > 已绑定 > 环境中唯一的一个。"""
    if wanted:
        return _normalize_device(c, wanted), 0
    env = c.get("/api/usb/environment")
    if not env.get("installed"):
        return None, ctx_error(_environment_text(env))
    devices = env.get("devices") or []
    bound = settings.usb_capture_hub()
    if bound and any(item.get("id") == bound for item in devices):
        return bound, 0
    if len(devices) == 1:
        return devices[0].get("id"), 0
    if not devices:
        return None, ctx_error(
            "未枚举到 USBPcap root hub。请确认已安装 USBPcap 并重启，"
            "然后执行 `omni usb capture doctor` 查看详情。"
        )
    listing = "\n".join(
        f"  {item.get('id')}  {item.get('label')}" for item in devices
    )
    return None, ctx_error(f"检测到多个 root hub，请用 --device 指定：\n{listing}")


def _capture_bind(c: ApiClient, args):
    """绑定默认 root hub，之后 ``capture start`` 不用再传 --device。"""
    env = c.get("/api/usb/environment")
    if not env.get("installed"):
        return ctx_error(_environment_text(env))
    devices = [item for item in env.get("devices") or [] if item.get("id")]
    if args.device:
        device = _normalize_device(c, args.device)
        if device not in {item["id"] for item in devices}:
            listing = "\n".join(
                f"  {item.get('id')}  {item.get('label')}" for item in devices
            )
            return ctx_error(f"没有这个 root hub：{device}\n可用：\n{listing}")
    else:
        if len(devices) != 1:
            listing = "\n".join(
                f"  {item.get('id')}  {item.get('label')}" for item in devices
            )
            return ctx_error(f"检测到多个 root hub，请用 --device 指定：\n{listing}")
        device = devices[0]["id"]

    settings.set_usb_capture_hub(device)
    finish(
        args,
        {"bound": device},
        f"已绑定 root hub: {device}\n"
        "之后 `omni usb capture start` 不用再传 --device。",
    )
    return 0


def _capture_unbind(c: ApiClient, args):
    previous = settings.usb_capture_hub()
    settings.set_usb_capture_hub(None)
    text = "已取消 root hub 绑定。"
    if previous:
        text = f"已取消绑定（原 {previous}）。"
    finish(args, {"bound": None}, text + "\n`capture start` 恢复自动选择/多 hub 报错。")
    return 0


def _capture_start(c: ApiClient, args):
    device, code = _pick_device(c, args.device)
    if device is None:
        return code
    payload = {
        "device_id": device,
        "usbpcap_path": args.usbpcap_path,
        "snaplen": args.snaplen,
        "bufferlen": args.bufferlen,
        "devices": args.devices,
    }
    data = c.post("/api/usb/capture/start", payload)
    if not data.get("success", True):
        return _failure(data, "开始捕获失败")
    finish(
        args,
        data,
        f"已开始捕获 {device}\n抓包文件: {data.get('capturePath') or '-'}\n"
        "提示: 建议先开始捕获再插拔目标设备，这样能抓到完整枚举。",
    )
    return 0


def _capture_stop(c: ApiClient, args):
    data = c.post("/api/usb/capture/stop")
    if not data.get("success", True):
        return _failure(data, "停止捕获失败")
    totals = data.get("totals") or {}
    finish(
        args,
        data,
        f"已停止捕获（{totals.get('transfers', 0)} 条传输，"
        f"{totals.get('bytes', 0)} 字节）",
    )
    return 0


def _capture_status(c: ApiClient, args):
    data = c.get("/api/usb/capture/status")
    totals = data.get("totals") or {}
    mode = data.get("mode") or "-"
    running = "是" if data.get("running") else ("解析中" if data.get("loading") else "否")
    text = "\n".join(
        [
            f"运行中      : {running}",
            f"模式        : {mode}",
            f"设备        : {data.get('deviceId') or '-'}",
            f"开始时间    : {data.get('startedAt') or '-'}",
            f"抓包文件    : {data.get('capturePath') or '-'}",
            f"传输        : {totals.get('transfers', 0)}",
            f"字节        : {totals.get('bytes', 0)}",
            f"错误        : {totals.get('errors', 0)}",
            f"未匹配      : {totals.get('unmatched', 0)}",
            f"缓冲行      : {data.get('buffered', 0)} / {data.get('ringSize', 0)}",
        ]
    )
    if data.get("lastError"):
        text += f"\n最近错误    : {data['lastError']}"
    finish(args, data, text)
    return 0


def _transfer_query(args, after_id: int | None) -> str:
    parts = [f"limit={int(args.limit)}"]
    if after_id is not None:
        parts.append(f"after_id={int(after_id)}")
    if getattr(args, "type", None):
        parts.append("type=" + args.type)
    if getattr(args, "dir", None):
        parts.append("dir=" + args.dir)
    for key, value in (("bus", args.bus), ("device", args.device), ("endpoint", args.endpoint)):
        if value is not None:
            parts.append(f"{key}={int(value)}")
    if args.status:
        parts.append("status=" + args.status)
    if getattr(args, "vid", None):
        parts.append("vid=" + args.vid)
    if getattr(args, "pid", None):
        parts.append("pid=" + args.pid)
    if args.q:
        from urllib.parse import quote

        parts.append("q=" + quote(args.q))
    return "&".join(parts)


def _missing_preview(items: list[dict]) -> bool:
    """所有非空传输都没有 dataHex：说明后端版本较旧，列表里拿不到 payload。"""
    seen = False
    for item in items:
        if (item.get("length") or 0) > 0:
            seen = True
            if item.get("dataHex"):
                return False
    return seen


def _vid_pid_label(vid, pid) -> str | None:
    """把过滤条件拼成 ``0xC251:0x0005`` / ``0xC251:`` / ``:0x0005``，用于匹配设备树。"""
    v = _usb_id_value(vid) if vid else None
    p = _usb_id_value(pid) if pid else None
    if v is None and p is None:
        return None
    left = f"0x{v:04X}" if v is not None else ""
    right = f"0x{p:04X}" if p is not None else ""
    return f"{left}:{right}"


def _device_tree_text(c: ApiClient) -> str:
    try:
        data = c.get("/api/usb/devices")
    except Exception:
        return ""
    return "\n".join(_render_tree(data.get("tree") or []))


def _empty_transfers_hint(c: ApiClient, args) -> str:
    """没有结果时区分「没抓到数据」和「VID/PID 过滤没命中」。"""
    base = (
        "没有匹配的传输。抓包时先 `omni usb capture start`，"
        "再插拔/操作设备；离线分析用 `omni usb capture open --pcap F`。"
    )
    if not (getattr(args, "vid", None) or getattr(args, "pid", None)):
        return base
    want = f"{args.vid or '-'}:{args.pid or '-'}"
    try:
        peek = c.get("/api/usb/transfers?limit=1")
        total = int(peek.get("total") or 0)
    except Exception:
        total = 0
    if not total:
        return base
    return (
        f"抓包里有 {total} 条传输，但没有 {want} 的匹配。\n"
        "  设备如果在 capture start 之前就已枚举，抓包里没有它的描述符，VID/PID 映射不到；\n"
        "  先 `omni usb capture stop` 再 `start`，然后插拔设备。\n"
        "  也可以 `omni usb capture devices` 确认抓到了哪些设备。"
    )


def _capture_transfers(c: ApiClient, args):
    if not args.follow:
        data = c.get(f"/api/usb/transfers?{_transfer_query(args, args.since)}")
        items = data.get("items") or []
        if args.json_:
            finish(args, data, "")
            return 0
        if not items:
            if not args.quiet:
                print(_empty_transfers_hint(c, args))
            return 0
        if args.data_bytes and _missing_preview(items):
            print(
                "提示: 当前后端未返回 payload 预览，数据列显示为 -（升级 OMNI Bus 后端后可用）",
                file=sys.stderr,
            )
        _print_transfers(items, args.data_bytes, args.data_per_line, not args.no_setup)
        return 0

    # --follow：用 after_id 轮询，Ctrl+C 正常退出
    if args.since is not None:
        cursor = args.since
    else:
        # 默认只跟新传输：先取当前 nextId，避免把环形缓存里的历史整段刷出来。
        # 需要回放历史时显式 --since 0。
        peek = c.get("/api/usb/transfers?limit=1")
        cursor = int(peek.get("nextId") or 0)
    if not args.quiet and (args.vid or args.pid):
        want = _vid_pid_label(args.vid, args.pid)
        if want and want not in _device_tree_text(c):
            print(
                f"提示: 当前抓包里还没有 {want} 的描述符，过滤不会命中。\n"
                "  设备如果在 capture start 之前就已枚举，抓包里没有它的 GET_DESCRIPTOR；\n"
                "  先 `omni usb capture stop` 再 `start`，然后重新插拔设备。\n"
                "  重新插拔后仍没有，说明它挂在另一个 root hub："
                "capture bind --device USBPcap2 再试。",
                file=sys.stderr,
            )

    interval = max(0.05, float(args.interval))
    deadline = time.monotonic() + args.duration if args.duration else None
    follow_layout: tuple[list[str], int, list[int]] | None = None
    preview_warned = False
    waited_hint = False
    try:
        while True:
            data = c.get(f"/api/usb/transfers?{_transfer_query(args, cursor)}")
            items = data.get("items") or []
            if items:
                if args.json_:
                    for item in items:
                        print(json.dumps(item, ensure_ascii=False))
                else:
                    if (
                        args.data_bytes
                        and not preview_warned
                        and _missing_preview(items)
                    ):
                        print(
                            "提示: 当前后端未返回 payload 预览，数据列显示为 -"
                            "（升级 OMNI Bus 后端后可用）",
                            file=sys.stderr,
                        )
                        preview_warned = True
                    if follow_layout is None:
                        follow_layout = _follow_layout(
                            args.data_bytes, _terminal_columns(), args.data_per_line
                        )
                        if not args.quiet:
                            print(
                                render_table(
                                    follow_layout[0], [], widths=follow_layout[2]
                                )
                            )
                    headers, per_line, widths = follow_layout
                    print(
                        render_table(
                            headers,
                            _transfer_rows(items, args.data_bytes, per_line, not args.no_setup),
                            widths=widths,
                            show_header=False,
                        )
                    )
                cursor = int(items[-1]["id"])
                sys.stdout.flush()
            elif not args.json_ and not waited_hint and not args.quiet:
                if getattr(args, "vid", None) or getattr(args, "pid", None):
                    want = f"{args.vid or '-'}:{args.pid or '-'}"
                    print(f"等待匹配 {want} 的传输…（Ctrl+C 结束）", file=sys.stderr)
                else:
                    print("等待传输…（Ctrl+C 结束）", file=sys.stderr)
                waited_hint = True

            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        if not args.quiet:
            print("\n已停止跟踪", file=sys.stderr)
        return 0
    return 0


def _render_tree(nodes: list[dict], indent: int = 0) -> list[str]:
    lines = []
    pad = "  " * indent
    for node in nodes:
        title = node.get("title") or node.get("label") or ""
        value = node.get("value")
        lines.append(f"{pad}{title}: {value}" if value else f"{pad}{title}")
        for field in node.get("fields") or []:
            lines.append(f"{pad}  {field.get('name')}: {field.get('value')}")
        lines.extend(_render_tree(node.get("children") or [], indent + 1))
    return lines


def _capture_detail(c: ApiClient, args):
    data = c.get(f"/api/usb/transfers/{int(args.transfer_id)}")
    if not data.get("success", True):
        return _failure(data, "详情不可用")
    if args.json_:
        finish(args, data, "")
        return 0

    lines = [
        f"#{data.get('id')}  {data.get('typeLabel')} "
        f"{str(data.get('direction', '')).upper()} 0x{int(data.get('endpoint', 0)):02X}  "
        f"{data.get('statusName')}  {data.get('length')} B  {_ts_text(data.get('ts'))}",
        "",
    ]
    if data.get("setupHex"):
        lines.append(f"SETUP: {data['setupHex']}")
    lines.append(f"摘要 : {data.get('summary') or '-'}")
    tree = data.get("decodeTree") or []
    if tree:
        lines.append("")
        lines.append("解码:")
        lines.extend("  " + line for line in _render_tree(tree))
    hex_text = data.get("dataHex") or ""
    if hex_text:
        lines.append("")
        lines.append("数据:")
        hex_bytes = hex_text.split()
        for offset in range(0, len(hex_bytes), 16):
            chunk = hex_bytes[offset : offset + 16]
            ascii_part = ""
            for byte in chunk:
                value = int(byte, 16)
                ascii_part += chr(value) if 32 <= value < 127 else "."
            lines.append(
                f"  {offset:04X}  {' '.join(chunk):<47}  {ascii_part}"
            )
        if data.get("dataTruncated"):
            lines.append(
                f"  … 共 {data.get('dataLength')} 字节，仅显示前 {len(hex_bytes)} 字节；"
                "完整数据请导出 pcap。"
            )
    finish(args, data, "\n".join(lines))
    return 0


def _capture_devices(c: ApiClient, args):
    data = c.get("/api/usb/devices")
    tree = data.get("tree") or []
    if args.json_:
        finish(args, data, "")
        return 0
    if not tree:
        finish(
            args,
            data,
            "没有捕获到描述符。抓包开始晚于设备枚举时会出现这种情况："
            "先 start，再插拔设备。",
        )
        return 0
    finish(args, data, "\n".join(_render_tree(tree)))
    return 0


def _capture_open(c: ApiClient, args):
    data = c.post("/api/usb/capture/open", {"path": args.pcap})
    if not data.get("success", True):
        return _failure(data, "打开 pcap 失败")
    if args.no_wait:
        finish(args, data, f"已开始解析 {args.pcap}（后台进行）")
        return 0

    deadline = time.monotonic() + 1800  # 解析大文件可能需要几分钟
    # 进度只在终端里原地刷新；管道/重定向（CI、日志）下不输出，避免刷屏噪音。
    show_progress = not args.quiet and sys.stderr.isatty()
    last_percent = -1
    try:
        while time.monotonic() < deadline:
            status = c.get("/api/usb/capture/status")
            if not status.get("loading"):
                break
            progress = status.get("progress") or {}
            percent = int(progress.get("percent", -1))
            if show_progress and percent >= 0 and percent != last_percent:
                print(f"\r解析中 {percent}%", end="", file=sys.stderr, flush=True)
                last_percent = percent
            time.sleep(0.2)
        else:
            return ctx_error(
                "解析超时（30 分钟）。可用 --no-wait 先返回，再用 "
                "`omni usb capture status` 查看进度。"
            )
    except KeyboardInterrupt:
        if not args.quiet:
            print("\n已停止等待（后台解析继续）", file=sys.stderr)
        return 0
    if show_progress and last_percent >= 0:
        print(file=sys.stderr)  # 结束原地刷新的那一行

    status = c.get("/api/usb/capture/status")
    if status.get("lastError"):
        return ctx_error(status["lastError"])
    totals = status.get("totals") or {}
    if args.json_:
        finish(args, status, "")
        return 0
    finish(
        args,
        status,
        f"已解析 {args.pcap}\n传输: {totals.get('transfers', 0)}  "
        f"错误: {totals.get('errors', 0)}  未匹配: {totals.get('unmatched', 0)}",
    )
    return 0


def _capture_save(c: ApiClient, args):
    data = c.post("/api/usb/capture/save", {"path": args.out})
    if not data.get("success", True):
        return _failure(data, "导出失败")
    finish(args, data, f"已导出到 {data.get('path') or args.out}")
    return 0


def _load_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取 {path}：{exc}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 不是合法 JSON：{exc}") from None


_CLASS_NAMES = {
    0x00: "defined-by-interface", 0x01: "Audio", 0x02: "CDC-Control", 0x03: "HID",
    0x07: "Printer", 0x08: "Mass-Storage", 0x09: "Hub", 0x0A: "CDC-Data",
    0x0B: "Smart-Card", 0x0D: "Content-Security", 0x0E: "Video",
    0x0F: "Personal-Healthcare", 0x10: "Audio/Video", 0x11: "Billboard",
    0x12: "Type-C-Bridge", 0xDC: "Diagnostic", 0xE0: "Wireless",
    0xEF: "Miscellaneous", 0xFE: "Application-Specific", 0xFF: "Vendor-Specific",
}

_CDC_FUNC_NAMES = {
    0x00: "Header", 0x01: "Call-Management", 0x02: "ACM", 0x03: "Direct-Line",
    0x04: "Telephone-Ringing", 0x05: "RNDIS", 0x06: "Union", 0x07: "Country-Selection",
    0x08: "Telephone-Modes", 0x09: "USB-Terminal", 0x0A: "Net-Channel-Terminal",
    0x0B: "Protocol-Unit", 0x0C: "Extension-Unit", 0x0D: "Multi-Channel",
    0x0E: "CAPI-Control", 0x0F: "Ethernet-Networking", 0x10: "ATM-Networking",
    0x11: "WHCM", 0x12: "Device-Management", 0x13: "OBEX", 0x14: "Command-Set",
    0x15: "Command-Set-Rev", 0x16: "Telephony-Data", 0x17: "MDLM", 0x18: "Table",
    0x1A: "NCM", 0x1B: "MBIM",
}


def _int_or_none(value):
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


def _class_label(value) -> str:
    """0xEF -> 'Miscellaneous(0xEF)'；认不出时原样返回。"""
    number = _int_or_none(value)
    if number is None:
        return str(value)
    name = _CLASS_NAMES.get(number)
    return f"{name}(0x{number:02X})" if name else f"0x{number:02X}"


def _class_desc_label(item: dict) -> str:
    """给类描述符一个语义名：IAD / CDC 功能描述符 / 其它。"""
    desc_type = _int_or_none(item.get("type"))
    subtype = _int_or_none(item.get("subtype"))
    if desc_type == 0x0B:
        first = item.get("firstInterface", "?")
        count = item.get("interfaceCount", "?")
        return (
            f"IAD 接口关联: iface {first}..{int(first) + int(count) - 1 if isinstance(first, int) and isinstance(count, int) else '?'}"
            f"，类 {_class_label(item.get('class'))}/{item.get('subClass')}/{item.get('protocol')}"
            f"，iFunction {item.get('iFunction')}"
        )
    if desc_type == 0x24:
        name = _CDC_FUNC_NAMES.get(subtype) if subtype is not None else None
        return f"CDC 功能: {name or 'subtype-' + str(subtype)}"
    return f"类描述符 {item.get('type')}"


def _document_lines(doc: dict) -> list[str]:
    """把描述符文档渲染成人读的摘要（不展开原始 hex，hex 由 build/emit 给）。"""
    lines: list[str] = []
    device = doc.get("device") or {}
    if device:
        lines.append(
            "设备: VID {vid}  PID {pid}  bcdUSB {bcd}  类 {cls}/{sub}/{proto}  EP0 {ep}".format(
                vid=device.get("vid", "-"),
                pid=device.get("pid", "-"),
                bcd=device.get("bcdUSB", "-"),
                cls=device.get("class", 0),
                sub=device.get("subClass", 0),
                proto=device.get("protocol", 0),
                ep=device.get("maxPacketSize0", "-"),
            )
        )
    for index, config in enumerate(doc.get("configurations") or [], 1):
        interfaces = config.get("interfaces") or []
        lines.append(
            f"配置 {config.get('value', index)}: {config.get('maxPower', 0)} mA，"
            f"{len(interfaces)} 接口，属性 {config.get('attributes', '-')}，"
            f"iConfiguration {config.get('iConfiguration', 0)}"
        )
        for item in config.get("classDescriptors") or []:
            lines.append(f"  {_class_desc_label(item)}  hex {item.get('hex', '-')}")
        for interface in interfaces:
            for alt in interface.get("alternates") or []:
                lines.append(
                    f"  接口 {interface.get('number')} alt {alt.get('alternate', 0)}: "
                    f"class {_class_label(alt.get('class'))}/{alt.get('subClass', 0)}/"
                    f"{alt.get('protocol', 0)}，{len(alt.get('endpoints') or [])} 端点，"
                    f"iInterface {alt.get('iInterface', 0)}"
                )
                for descriptor in alt.get("classDescriptors") or []:
                    lines.append(
                        f"    {_class_desc_label(descriptor)}  hex {descriptor.get('hex', '-')}"
                    )
                for endpoint in alt.get("endpoints") or []:
                    address = endpoint.get("address", "0x00")
                    value = int(str(address), 0)
                    direction = "IN" if value & 0x80 else "OUT"
                    kind = {0: "control", 1: "iso", 2: "bulk", 3: "interrupt"}.get(
                        int(str(endpoint.get("attributes", "0")), 0) & 0x03, "?"
                    )
                    lines.append(
                        f"    端点 {address} {direction} {kind} "
                        f"max {endpoint.get('maxPacketSize')} interval {endpoint.get('interval')}"
                    )
    strings = doc.get("strings") or {}
    if strings:
        lines.append("字符串:")
        for lang, table in strings.items():
            for index, text in table.items():
                lines.append(f"  {lang} [{index}] {text}")
    return lines


def _setup_lines(setup: dict, raw_hex: str) -> list[str]:
    lines = [
        f"SETUP {raw_hex}",
        f"  {setup.get('requestName')}  {str(setup.get('direction', '')).upper()}  "
        f"{setup.get('type')} → {setup.get('recipient')}",
        f"  wValue 0x{int(setup.get('wValue', 0)):04X}  "
        f"wIndex 0x{int(setup.get('wIndex', 0)):04X}  "
        f"wLength {setup.get('wLength', 0)}",
    ]
    decoded = setup.get("decoded") or {}
    if decoded:
        lines.append("  解码: " + "  ".join(f"{key}={value}" for key, value in decoded.items()))
    return lines


def _desc_parse(c: ApiClient, args):
    if args.hex:
        payload = {"hex": args.hex}
    elif args.file:
        try:
            data = Path(args.file).read_bytes()
        except OSError as exc:
            return ctx_error(f"无法读取 {args.file}：{exc}")
        payload = {"dataB64": base64.b64encode(data).decode("ascii")}
    else:
        payload = {"hex": sys.stdin.read()}

    data = c.post("/api/usb/desc/parse", payload)
    if not data.get("success"):
        return _failure(data, "解析失败")
    if args.json_:
        finish(args, data, "")
        return 0

    kind = data.get("kind")
    if kind == "setup":
        text = "\n".join(_setup_lines(data.get("setup") or {}, data.get("hex") or ""))
    elif data.get("document"):
        text = "\n".join(_document_lines(data["document"]))
    elif kind == "string":
        text = f"字符串（{data.get('length')} 字节）: {data.get('text')!r}"
    elif kind == "qualifier":
        text = "设备限定符: " + "  ".join(
            f"{key}={value}" for key, value in (data.get("fields") or {}).items()
        )
    else:
        text = f"未识别的数据（{data.get('length')} 字节）: {data.get('hex')}"
    finish(args, data, text)
    return 0


def _desc_validate(c: ApiClient, args):
    try:
        document = _load_json(args.file)
    except ValueError as exc:
        return ctx_error(str(exc))
    data = c.post("/api/usb/desc/validate", {"document": document})
    if not data.get("success"):
        return _failure(data, "校验失败")
    if args.json_:
        finish(args, data, "")
        return 0 if data.get("ok") else 1

    lines = []
    for item in data.get("errors") or []:
        lines.append(f"错误 [{item.get('code')}] {item.get('path')}: {item.get('message')}")
    for item in data.get("warnings") or []:
        lines.append(f"警告 [{item.get('code')}] {item.get('path')}: {item.get('message')}")
    if data.get("ok"):
        lines.append(f"校验通过（{len(data.get('warnings') or [])} 条警告）")
    finish(args, data, "\n".join(lines))
    return 0 if data.get("ok") else 1


def _built_lines(data: dict) -> list[str]:
    lines = []
    if data.get("device"):
        lines.append(f"设备        : {data['device']}")
    for item in data.get("configurations") or []:
        lines.append(f"配置 {item.get('value')} ({item.get('totalLength')} B): {item.get('hex')}")
    for item in data.get("strings") or []:
        lines.append(f"字符串 {item.get('index')} ({item.get('langId')}): {item.get('hex')}")
    if data.get("bos"):
        lines.append(f"BOS         : {data['bos']}")
    validation = data.get("validation") or {}
    errors = len(validation.get("errors") or [])
    warnings = len(validation.get("warnings") or [])
    lines.append(f"校验        : {'通过' if validation.get('ok') else '未通过'}（{errors} 错误，{warnings} 警告）")
    for item in validation.get("errors") or []:
        lines.append(f"  错误 [{item.get('code')}] {item.get('message')}")
    return lines


def _desc_build(c: ApiClient, args):
    try:
        document = _load_json(args.file)
    except ValueError as exc:
        return ctx_error(str(exc))
    data = c.post("/api/usb/desc/build", {"document": document})
    if not data.get("success"):
        return _failure(data, "生成失败")

    if args.out:
        try:
            Path(args.out).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            return ctx_error(f"无法写入 {args.out}：{exc}")

    if args.json_:
        finish(args, data, "")
    else:
        finish(args, data, "\n".join(_built_lines(data)))
    return 0 if (data.get("validation") or {}).get("ok") else 1


def _desc_emit(c: ApiClient, args):
    try:
        document = _load_json(args.file)
    except ValueError as exc:
        return ctx_error(str(exc))
    data = c.post("/api/usb/desc/build", {"document": document})
    if not data.get("success"):
        return _failure(data, "生成失败")

    if args.format == "json":
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        lines = [data["device"]] if data.get("device") else []
        lines.extend(item["hex"] for item in data.get("configurations") or [])
        lines.extend(item["hex"] for item in data.get("strings") or [])
        if data.get("bos"):
            lines.append(data["bos"])
        text = "\n".join(lines)

    if args.out:
        try:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            return ctx_error(f"无法写入 {args.out}：{exc}")
    if not args.quiet:
        print(text)
    return 0 if (data.get("validation") or {}).get("ok") else 1


def _desc_template_list(c: ApiClient, args):
    data = c.get("/api/usb/desc/templates")
    items = data.get("templates") or []
    if args.json_:
        finish(args, data, "")
        return 0
    print_table(["模板", "说明"], [[item.get("name"), item.get("description")] for item in items])
    return 0


def _desc_template_show(c: ApiClient, args):
    data = c.get(f"/api/usb/desc/templates/{args.name}")
    if not data.get("success", True):
        return _failure(data, "模板不存在")
    document = data.get("document") or {}
    finish(
        args,
        document,
        "\n".join(_document_lines(document)) or json.dumps(document, ensure_ascii=False, indent=2),
    )
    return 0


def _desc_from_capture(c: ApiClient, args):
    payload: dict = {"path": args.pcap}
    if args.bus is not None:
        payload["bus"] = args.bus
    if args.device is not None:
        payload["device"] = args.device
    data = c.post("/api/usb/desc/from-capture", payload)
    if not data.get("success", True):
        return _failure(data, "反推失败")
    documents = data.get("documents") or []
    if args.json_:
        finish(args, data, "")
        return 0
    if not documents:
        finish(
            args,
            data,
            "pcap 中没有完整的描述符。抓包需覆盖设备枚举"
            "（先 start 再插拔），或用 `omni usb capture open` 确认能解析到 GET_DESCRIPTOR。",
        )
        return 0
    blocks = []
    for item in documents:
        blocks.append(f"=== bus {item['bus']} device {item['device']} ===")
        blocks.extend(_document_lines(item["document"]))
    finish(args, data, "\n".join(blocks))
    return 0


def _test_run(c: ApiClient, args):
    try:
        case = _load_json(args.file)
    except ValueError as exc:
        return ctx_error(str(exc))

    # pcap 相对路径按用例文件所在目录解析，避免依赖调用者的 cwd
    pcap = case.get("pcap")
    if pcap and not Path(str(pcap)).is_absolute():
        case["pcap"] = str((Path(args.file).resolve().parent / str(pcap)).resolve())

    payload = {
        "case": case,
        "junit": bool(args.junit),
        "stopOnFailure": not args.keep_going,
    }
    data = c.post("/api/usb/test/run", payload)
    if not data.get("success", True):
        return _failure(data, "用例执行失败")

    if args.junit:
        xml = data.get("junit")
        if not xml:
            return ctx_error("后端没有返回 JUnit 报告（确认后端版本支持 --junit）")
        try:
            Path(args.junit).write_text(xml, encoding="utf-8")
        except OSError as exc:
            return ctx_error(f"无法写入 {args.junit}：{exc}")

    if args.json_:
        finish(args, data, "")
    else:
        report = data
        summary = report.get("summary") or {}
        lines = [
            f"用例 {report.get('name')}: {'PASS' if report.get('ok') else 'FAIL'}"
            f"（{summary.get('passed', 0)}/{summary.get('steps', 0)} 步，"
            f"{report.get('durationMs', 0)} ms）"
        ]
        for step in report.get("steps") or []:
            mark = "PASS" if step.get("ok") else "FAIL"
            lines.append(
                f"  {mark}  [{step.get('index')}] {step.get('name')} — {step.get('detail')}"
            )
        if args.junit:
            lines.append(f"JUnit 已写入 {args.junit}")
        finish(args, data, "\n".join(lines))
    return 0 if data.get("ok") else 1


def _test_list(c: ApiClient, args):
    data = c.get("/api/usb/test/schema")
    example = data.get("example") or {}
    finish(
        args,
        data,
        "用例格式（离线 pcap 断言 / 真机动作）:\n"
        + json.dumps(example, ensure_ascii=False, indent=2)
        + "\n\n离线断言: transfers / transfer / device / errors / descriptor\n"
        "真机动作: open / control / write / read / echo / close（"
        "device.channel = winusb | hid | cdc）\n"
        "动作返回值断言: ok / length / minLength / hex / hexStartsWith / mismatches / bytesWritten\n"
        "Example: omni usb test run --file case.json --junit report.xml",
    )
    return 0


def _cdc_list(c: ApiClient, args):
    data = c.get("/api/serial/ports")
    # 只保留 USB 设备（pyserial 能给出 VID/PID 的就是 USB 串口/CDC）
    ports = [item for item in (data.get("ports") or []) if item.get("vid") or item.get("pid")]
    if args.json_:
        finish(args, {"ports": ports}, "")
        return 0
    if not ports:
        finish(
            args,
            {"ports": []},
            "未发现 USB CDC 设备。确认设备已枚举（设备管理器里出现 COM 口），"
            "或用 `omni serial ports` 查看全部串口。",
        )
        return 0
    rows = [
        [
            item.get("device"),
            item.get("description"),
            f"{item.get('vid') or '-'}:{item.get('pid') or '-'}",
            item.get("manufacturer") or "-",
        ]
        for item in ports
    ]
    print_table(["端口", "描述", "VID:PID", "厂商"], rows)
    return 0


def _cdc_open(c: ApiClient, args):
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
        return _failure(data, "打开 CDC 设备失败")
    finish(
        args,
        data,
        f"CDC 已打开: {args.port} @ {args.baud} "
        f"{args.bytesize}{args.parity}{int(args.stopbits)}"
        f"（实例 {args.id}）",
    )
    return 0


def _cdc_send(c: ApiClient, args):
    sources = [item for item in (args.text, args.hex, args.file) if item]
    if len(sources) != 1:
        return ctx_error("需要且只能指定一种数据来源：--text / --hex / --file")
    if args.text is not None:
        data = args.text.encode("utf-8")
        if not args.no_newline:
            data += b"\n"
    elif args.hex is not None:
        try:
            data = _parse_hex(args.hex)
        except ValueError as exc:
            return ctx_error(f"无效的十六进制字节: {exc}")
    else:
        try:
            data = Path(args.file).read_bytes()
        except OSError as exc:
            return ctx_error(f"无法读取文件 {args.file}: {exc}")

    resp = c.post(
        "/api/serial/send", {"connection_id": args.id, "data": data.hex().upper()}
    )
    if not resp.get("success", True):
        return _failure(resp, "发送失败")
    preview = " ".join(f"{byte:02X}" for byte in data[:16])
    if len(data) > 16:
        preview += " …"
    finish(args, resp, f"已发送 {len(data)} 字节（实例 {args.id}）  [{preview}]")
    return 0


def _cdc_read(c: ApiClient, args):
    if args.wait is not None:
        if args.follow:
            return ctx_error("--wait 是一次性同步等读，不能与 --follow 同用")

        def _fetch(since: int, max_bytes: int) -> dict:
            return c.post(
                "/api/serial/read",
                {
                    "connection_id": args.id,
                    "since": since,
                    "max_bytes": max_bytes,
                    "timeout_ms": int(args.wait),
                },
            )

    else:

        def _fetch(since: int, max_bytes: int) -> dict:
            return c.get(
                f"/api/serial/data?connection_id={args.id}"
                f"&since={since}&max_bytes={max_bytes}"
            )

    return run_cursor_stream(
        args,
        _fetch,
        subject="CDC",
        empty_hint="（暂无数据：确认设备在发送、波特率/校验位匹配，"
        "或先 `omni usb cdc open`）",
        cursor_key=f"cdc:{args.id}",
        service_name="bus",
    )


def _cdc_break(c: ApiClient, args):
    data = c.post(
        "/api/serial/break",
        {"connection_id": args.id, "duration_ms": int(args.duration_ms)},
    )
    if not data.get("success", True):
        return _failure(data, "发送 BREAK 失败")
    finish(args, data, f"已发送 BREAK（{args.duration_ms} ms，实例 {args.id}）")
    return 0


def _cdc_status(c: ApiClient, args):
    data = c.get(f"/api/serial/status?connection_id={args.id}")
    if args.json_:
        finish(args, data, "")
        return 0
    if not data.get("connected"):
        finish(args, data, f"CDC 未打开（实例 {args.id}）。")
        return 1
    capture = data.get("capture") or {}
    finish(
        args,
        data,
        f"已打开  {data.get('port')} @ {data.get('baudrate')} "
        f"{data.get('bytesize')}{data.get('parity')}{int(data.get('stopbits', 1))}"
        f"（实例 {args.id}）\n"
        f"接收缓冲: {capture.get('buffered_bytes', 0)} 字节"
        f"（oldest_seq={capture.get('oldest_seq')} next_seq={capture.get('next_seq')}）",
    )
    return 0


def _cdc_close(c: ApiClient, args):
    data = c.post("/api/serial/close", {"connection_id": args.id})
    if not data.get("success", True):
        return _failure(data, "关闭失败")
    finish(args, data, f"CDC 已关闭（实例 {args.id}）")
    return 0


def _as_number(text: str, field: str) -> int:
    if text is None or text == "":
        return 0
    try:
        return int(str(text), 0)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 需要数字（如 0xC251）：{text}") from None


def _hid_list(c: ApiClient, args):
    data = c.get("/api/usb/hid/devices")
    if not data.get("success", True):
        return _failure(data, "HID 枚举失败")
    devices = data.get("devices") or []
    if args.vid:
        devices = [item for item in devices if item.get("vid", "").lower() == args.vid.lower()]
    if args.pid:
        devices = [item for item in devices if item.get("pid", "").lower() == args.pid.lower()]
    if args.json_:
        finish(args, {"devices": devices}, "")
        return 0
    if not devices:
        finish(args, {"devices": []}, "未发现匹配的 HID 设备。")
        return 0
    rows = [
        [
            f"{item.get('vid')}:{item.get('pid')}",
            item.get("product") or "-",
            item.get("manufacturer") or "-",
            str(item.get("inputReportLength") or 0),
            str(item.get("outputReportLength") or 0),
            str(item.get("featureReportLength") or 0),
            item.get("serial") or "-",
        ]
        for item in devices
    ]
    print_table(["VID:PID", "产品", "厂商", "IN", "OUT", "Feature", "序列号"], rows)
    return 0


def _hid_open(c: ApiClient, args):
    try:
        vid = _as_number(args.vid, "--vid")
        pid = _as_number(args.pid, "--pid")
    except ValueError as exc:
        return ctx_error(str(exc))
    payload = {
        "id": args.id,
        "vid": vid,
        "pid": pid,
        "serial": args.serial,
        "path": args.path,
        "timeout_ms": args.timeout_ms,
    }
    data = c.post("/api/usb/hid/open", payload)
    if not data.get("success", True):
        return _failure(data, "打开 HID 设备失败")
    device = data.get("device") or {}
    finish(
        args,
        data,
        f"HID 已打开 {device.get('vid')}:{device.get('pid')} "
        f"{device.get('product') or ''}（实例 {args.id}）\n"
        f"报告长度: IN {device.get('inputReportLength')} / "
        f"OUT {device.get('outputReportLength')} / "
        f"Feature {device.get('featureReportLength')}",
    )
    return 0


def _hid_read(c: ApiClient, args):
    if args.count < 0:
        return ctx_error("--count 不能为负数")
    reports: list[dict] = []
    out_file = None
    if args.out:
        try:
            out_file = open(args.out, "ab")
        except OSError as exc:
            return ctx_error(f"无法写入 {args.out}：{exc}")
    try:
        for index in range(args.count):
            data = c.post(
                "/api/usb/hid/read",
                {
                    "id": args.id,
                    "length": args.length,
                    "timeout_ms": args.timeout_ms,
                },
            )
            if not data.get("success", True):
                return _failure(data, f"第 {index + 1} 次读取失败")
            raw = bytes.fromhex((data.get("dataHex") or "").replace(" ", ""))
            if out_file is not None:
                out_file.write(raw)
                out_file.flush()
            reports.append(data)
            if not args.json_ and not args.quiet:
                print(f"#{index + 1}  {len(raw)} 字节")
                if raw:
                    print(hexdump(raw))
    finally:
        if out_file is not None:
            out_file.close()

    if args.json_:
        finish(args, {"reports": reports}, "")
    elif not reports and not args.quiet:
        print("没有读到报告。")
    return 0


def _read_payload(args) -> tuple[bytes | None, str]:
    if args.data is not None:
        try:
            return _parse_hex(args.data), ""
        except ValueError as exc:
            return None, f"无效的十六进制字节: {exc}"
    if args.file:
        try:
            return Path(args.file).read_bytes(), ""
        except OSError as exc:
            return None, f"无法读取 {args.file}：{exc}"
    return b"", ""


def _hid_write(c: ApiClient, args):
    payload, problem = _read_payload(args)
    if problem:
        return ctx_error(problem)
    if not payload:
        return ctx_error("需要 --data（十六进制）或 --file")
    data = c.post(
        "/api/usb/hid/write",
        {
            "id": args.id,
            "hex": payload.hex().upper(),
            "report_id": args.report_id,
            "timeout_ms": args.timeout_ms,
        },
    )
    if not data.get("success", True):
        return _failure(data, "写报告失败")
    finish(
        args,
        data,
        f"已写报告 {data.get('bytesWritten')} 字节（report id {args.report_id}，实例 {args.id}）",
    )
    return 0


def _hid_feature(c: ApiClient, args):
    if args.get:
        data = c.post(
            "/api/usb/hid/feature/get",
            {"id": args.id, "report_id": args.report_id},
        )
        if not data.get("success", True):
            return _failure(data, "读取 Feature 报告失败")
        raw = bytes.fromhex((data.get("dataHex") or "").replace(" ", ""))
        if args.json_:
            finish(args, data, "")
        else:
            finish(args, data, hexdump(raw) if raw else "（空）")
        return 0

    payload, problem = _read_payload(args)
    if problem:
        return ctx_error(problem)
    if not payload:
        return ctx_error("--set 需要 --data（十六进制）或 --file")
    data = c.post(
        "/api/usb/hid/feature/set",
        {
            "id": args.id,
            "hex": payload.hex().upper(),
            "report_id": args.report_id,
        },
    )
    if not data.get("success", True):
        return _failure(data, "写 Feature 报告失败")
    finish(args, data, f"已写 Feature 报告 {len(payload)} 字节（report id {args.report_id}）")
    return 0


def _hid_status(c: ApiClient, args):
    data = c.get(f"/api/usb/hid/status?id={args.id}")
    if args.json_:
        finish(args, data, "")
        return 0
    if not data.get("connected"):
        finish(args, data, f"HID 会话未打开（实例 {args.id}）。")
        return 1
    device = data.get("device") or {}
    finish(
        args,
        data,
        f"已打开 {device.get('vid')}:{device.get('pid')} {device.get('product') or ''}"
        f"（实例 {args.id}，超时 {data.get('timeoutMs')} ms）",
    )
    return 0


def _hid_close(c: ApiClient, args):
    data = c.post("/api/usb/hid/close", {"id": args.id})
    if not data.get("success", True):
        return _failure(data, "关闭失败")
    finish(args, data, f"HID 已关闭（实例 {args.id}）")
    return 0


def _request_type(args) -> int:
    if args.request_type:
        return _as_number(args.request_type, "--request-type") & 0xFF
    direction = 0x80 if args.direction == "in" else 0
    kind = {"standard": 0x00, "class": 0x20, "vendor": 0x40}[args.req_type]
    recipient = {"device": 0, "interface": 1, "endpoint": 2, "other": 3}[args.recipient]
    return direction | kind | recipient


def _winusb_devices(c: ApiClient, guids: list[str] | None = None) -> dict:
    query = ""
    if guids:
        query = "?" + "&".join(f"guid={guid}" for guid in guids)
    return c.get(f"/api/usb/winusb/devices{query}")


def _format_device(device: dict) -> list[str]:
    lines = [
        f"设备  {device.get('vid')}:{device.get('pid')}  "
        f"{device.get('speed')}  bcdUSB {device.get('bcdUSB')}  "
        f"接口 {device.get('interfaceNumber')} class {device.get('class')}",
        f"路径  {device.get('path')}",
    ]
    for endpoint in device.get("endpoints") or []:
        lines.append(
            f"  {endpoint.get('address')} {str(endpoint.get('direction', '')).upper():3} "
            f"{endpoint.get('type'):11} max {endpoint.get('maxPacketSize'):4} "
            f"interval {endpoint.get('interval')}"
        )
    return lines


def _device_address(value: str) -> int:
    """传输列表的 ``--device`` 是设备地址，不是 root hub 路径。"""
    text = str(value).strip()
    if text.startswith("\\") or "usbpcap" in text.lower():
        raise argparse.ArgumentTypeError(
            "这里是设备地址（如 7，表格里 1.7 的后一段），不是 root hub；"
            "root hub 在 capture start --device '\\\\.\\USBPcap1' 里选"
        )
    try:
        return int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"设备地址应为整数（如 7），收到 {value!r}；表格里 1.7 的 7 就是它"
        ) from None


def _endpoint_address(value: str) -> int:
    """端点地址，接受 0x81 或 129 两种写法。"""
    text = str(value).strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"端点地址应为整数或 0x 十六进制（如 0x81 / 129），收到 {value!r}"
        ) from None


def _usb_id_value(value) -> int | None:
    """把 ``0xC251`` / ``C251`` 这类 VID/PID 写法转成整数。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        return int(text, 16)
    except ValueError:
        return None


def _capture_address_sort_key(address: str) -> tuple[int, int]:
    """按 ``bus.device`` 的数值而不是字符串排序。"""
    try:
        bus, device = str(address).split(".", 1)
        return int(bus), int(device)
    except (TypeError, ValueError):
        return sys.maxsize, sys.maxsize


def _captured_usb_devices(c: ApiClient) -> list[dict]:
    """读取当前实时抓包的设备地址映射。

    USBPcap 动态地址只对正在进行的实时抓包有意义。离线 pcap 或已停止的旧会话
    可能与 Windows 当前枚举出来的设备无关，因此不拿来装饰 ``usb list``。
    """
    try:
        status = c.get("/api/usb/capture/status")
        if not status.get("running") or status.get("mode") != "live":
            return []
        data = c.get("/api/usb/devices")
    except Exception:
        # 兼容没有抓包 API 的旧后端；系统 USB 枚举本身仍应正常工作。
        return []

    captured: list[dict] = []
    for node in data.get("tree") or []:
        if node.get("kind") != "device":
            continue
        fields = {
            str(field.get("name") or ""): str(field.get("value") or "")
            for field in node.get("fields") or []
        }
        ids = fields.get("VID:PID", "").split(":", 1)
        address = fields.get("地址", "")
        if len(ids) != 2 or "." not in address:
            continue
        vid, pid = _usb_id_value(ids[0]), _usb_id_value(ids[1])
        try:
            bus, device = (int(part) for part in address.split(".", 1))
        except ValueError:
            continue
        if vid is None or pid is None:
            continue
        captured.append(
            {
                "vid": vid,
                "pid": pid,
                "bus": bus,
                "device": device,
                "address": f"{bus}.{device}",
                "serial": fields.get("序列号") or None,
            }
        )
    return captured


def _device_hub_index(item: dict) -> int | None:
    """取 Windows 拓扑对应的 USBPcapN 序号。"""
    try:
        return int(item.get("hubIndex"))
    except (TypeError, ValueError):
        label = str(item.get("hubLabel") or "")
        suffix = label[7:] if label.lower().startswith("usbpcap") else ""
        return int(suffix) if suffix.isdigit() else None


def _with_capture_addresses(c: ApiClient, devices: list[dict]) -> list[dict]:
    """优先使用 hub IOCTL 地址，旧后端或查询失败时回退到实时抓包。"""
    need_fallback = any(
        item.get("vid")
        and item.get("pid")
        and not item.get("captureAddresses")
        for item in devices
    )
    captured = _captured_usb_devices(c) if need_fallback else []
    annotated: list[dict] = []
    for source in devices:
        item = dict(source)
        direct = sorted(
            {str(value) for value in item.get("captureAddresses") or []},
            key=_capture_address_sort_key,
        )
        if direct:
            item["captureAddresses"] = direct
            annotated.append(item)
            continue

        vid, pid = _usb_id_value(item.get("vid")), _usb_id_value(item.get("pid"))
        hub_index = _device_hub_index(item)
        matches = [
            record
            for record in captured
            if record["vid"] == vid
            and record["pid"] == pid
            and (hub_index is None or record["bus"] == hub_index)
        ]

        # 同一 hub 上有多个相同 VID/PID 时，真实 USB 序列号可以消除歧义。
        # 复合接口的 Windows 实例后缀并不一定是设备序列号，匹配不到时保留全部候选。
        serial = str(item.get("serial") or "")
        serial_matches = [record for record in matches if record.get("serial") == serial]
        if serial and serial_matches:
            matches = serial_matches

        item["captureAddresses"] = sorted(
            {record["address"] for record in matches}, key=_capture_address_sort_key
        )
        annotated.append(item)
    return annotated


def _capture_addresses_text(item: dict) -> str:
    return ",".join(str(value) for value in item.get("captureAddresses") or []) or "-"


def _all_usb_devices(c: ApiClient, args, *, vid=None, pid=None, driver=None):
    """列出系统 USB 设备，并关联当前 USBPcap 动态地址。"""
    from urllib.parse import quote

    query = "?service=" + quote(driver) if driver else ""
    data = c.get(f"/api/usb/winusb/all-devices{query}")
    if not data.get("success", True):
        return _failure(data, "USB 设备枚举失败")

    devices = data.get("devices") or []
    want_vid, want_pid = _usb_id_value(vid), _usb_id_value(pid)
    if want_vid is not None:
        devices = [item for item in devices if _usb_id_value(item.get("vid")) == want_vid]
    if want_pid is not None:
        devices = [item for item in devices if _usb_id_value(item.get("pid")) == want_pid]
    if driver:
        devices = [
            item
            for item in devices
            if (item.get("service") or "").lower() == driver.lower()
        ]
    devices = _with_capture_addresses(c, devices)
    payload = dict(data, devices=devices)

    if args.all:
        groups = _hub_groups(devices)
        if args.json_:
            finish(args, {"success": True, "hubs": groups}, "")
            return 0
        if not devices:
            finish(args, payload, "没有匹配的 USB 设备。")
            return 0
        _print_hub_tree(groups, settings.usb_capture_hub())
        return 0

    if args.json_:
        finish(args, payload, "")
        return 0
    if not devices:
        finish(args, payload, "没有匹配的 USB 设备。")
        return 0

    rows = [
        [
            f"{item.get('vid') or '-'}:{item.get('pid') or '-'}",
            item.get("description") or "-",
            item.get("service") or "-",
            item.get("hubLabel") or "-",
            _capture_addresses_text(item),
        ]
        for item in devices
    ]
    print_table(["VID:PID", "描述", "驱动服务", "所属 hub", "USB 地址"], rows)
    return 0


def _hub_groups(devices: list[dict]) -> list[list[dict]]:
    """按 root hub 分组；USBPcapN 在前，未识别的归到末尾。"""
    groups: dict[str, dict] = {}
    for item in devices:
        label = item.get("hubLabel") or "未知 hub"
        group = groups.setdefault(
            label,
            {
                "label": label,
                "device": item.get("hubDevice"),
                "controller": item.get("hubController"),
                "controllerId": item.get("hubControllerId"),
                "devices": [],
            },
        )
        group["devices"].append(item)

    def sort_key(group: dict):
        label = group["label"]
        if label.startswith("USBPcap") and label[7:].isdigit():
            return (0, int(label[7:]))
        return (1, 0)

    return sorted(groups.values(), key=sort_key)


def _print_hub_tree(groups: list[dict], bound: str | None) -> None:
    """树形展示：USB → USBPcapN（root hub）→ 设备。"""
    lines = ["USB"]
    if not groups:
        lines.append("└── 没有匹配的设备")
        print("\n".join(lines))
        return

    for group_index, group in enumerate(groups):
        group_last = group_index == len(groups) - 1
        head = group["label"]
        if group.get("controller"):
            head += "  " + group["controller"]
            if group.get("controllerId"):
                head += f" ({group['controllerId']})"
        if group.get("device"):
            head += "  " + group["device"]
        if bound and group.get("device") == bound:
            head += "  [已绑定]"
        lines.append(("└── " if group_last else "├── ") + head)

        child_prefix = "    " if group_last else "│   "
        devices = group["devices"]
        if not devices:
            lines.append(child_prefix + "└── 没有设备")
            continue
        for device_index, item in enumerate(devices):
            device_last = device_index == len(devices) - 1
            vid = f"{item.get('vid') or '-'}:{item.get('pid') or '-'}"
            lines.append(
                child_prefix
                + ("└── " if device_last else "├── ")
                + f"{vid}  {item.get('description') or '-'}  "
                + f"{item.get('service') or '-'}  "
                + f"USB 地址 {_capture_addresses_text(item)}"
            )
    print("\n".join(lines))


def _usb_list(c: ApiClient, args):
    return _all_usb_devices(c, args, vid=args.vid, pid=args.pid, driver=args.driver)


def _winusb_list(c: ApiClient, args):
    if args.all:
        return _all_usb_devices(c, args)

    data = _winusb_devices(c, args.guid)
    if not data.get("success", True):
        return _failure(data, "WinUSB 枚举失败")
    devices = data.get("devices") or []
    if args.json_:
        finish(args, data, "")
        return 0
    if not devices:
        finish(
            args,
            data,
            "未发现 WinUSB 接口。确认固件带 MS OS 2.0 描述符（或用 INF 绑定），"
            "并在设备管理器里确认它没有被其他驱动占用。",
        )
        return 0
    rows = [
        [
            f"{item.get('vid')}:{item.get('pid')}",
            item.get("speed"),
            item.get("interfaceNumber"),
            item.get("class"),
            len(item.get("endpoints") or []),
            item.get("path"),
        ]
        for item in devices
    ]
    print_table(["VID:PID", "速度", "接口", "Class", "端点数", "路径"], rows)
    return 0


def _winusb_info(c: ApiClient, args):
    data = _winusb_devices(c, args.guid)
    if not data.get("success", True):
        return _failure(data, "WinUSB 枚举失败")
    devices = data.get("devices") or []
    if args.index is not None:
        if not 0 <= args.index < len(devices):
            return ctx_error(f"设备序号越界：{args.index}（共 {len(devices)} 个）")
        devices = [devices[args.index]]
    if args.vid:
        devices = [item for item in devices if item.get("vid", "").lower() == args.vid.lower()]
    if args.pid:
        devices = [item for item in devices if item.get("pid", "").lower() == args.pid.lower()]
    if not devices:
        return ctx_error("没有匹配的 WinUSB 设备。")
    if args.json_:
        finish(args, {"devices": devices}, "")
        return 0
    blocks = []
    for device in devices:
        blocks.extend(_format_device(device))
        blocks.append("")
    finish(args, {"devices": devices}, "\n".join(blocks).rstrip())
    return 0


def _winusb_open(c: ApiClient, args):
    payload = {
        "id": args.id,
        "path": args.path,
        "vid": None if args.vid is None else _as_number(args.vid, "--vid"),
        "pid": None if args.pid is None else _as_number(args.pid, "--pid"),
        "interface": args.interface,
        "timeout_ms": args.timeout_ms,
    }
    data = c.post("/api/usb/winusb/open", payload)
    if not data.get("success", True):
        return _failure(data, "打开 WinUSB 接口失败")
    device = data.get("device") or {}
    finish(
        args,
        data,
        f"WinUSB 已打开 {device.get('vid')}:{device.get('pid')} "
        f"{device.get('speed')}（接口 {device.get('interfaceNumber')}，实例 {args.id}）",
    )
    return 0


def _winusb_close(c: ApiClient, args):
    data = c.post("/api/usb/winusb/close", {"id": args.id})
    if not data.get("success", True):
        return _failure(data, "关闭失败")
    finish(args, data, f"WinUSB 已关闭（实例 {args.id}）")
    return 0


def _winusb_status(c: ApiClient, args):
    data = c.get(f"/api/usb/winusb/status?id={args.id}")
    if args.json_:
        finish(args, data, "")
        return 0
    if not data.get("connected"):
        finish(args, data, f"WinUSB 会话未打开（实例 {args.id}）。")
        return 1
    device = data.get("device") or {}
    finish(
        args,
        data,
        f"已打开 {device.get('vid')}:{device.get('pid')} {device.get('speed')}"
        f"（实例 {args.id}，超时 {data.get('timeoutMs')} ms）",
    )
    return 0


def _capture_hub(c: ApiClient, wanted: str | None) -> tuple[str | None, str]:
    env = c.get("/api/usb/environment")
    if not env.get("supported"):
        return None, "抓包仅支持 Windows。"
    if not env.get("installed"):
        return None, "未安装 USBPcap：安装后重启，或用 --usbpcap-path 指定。"
    devices = env.get("devices") or []
    if wanted:
        return wanted, ""
    if len(devices) == 1:
        return devices[0].get("id"), ""
    if not devices:
        return None, "未枚举到 USBPcap root hub（确认已安装并重启）。"
    listing = "\n".join(f"  {item.get('id')}  {item.get('label')}" for item in devices)
    return None, f"检测到多个 root hub，请用 --capture-device 指定：\n{listing}"


def _capture_begin(c: ApiClient, args) -> tuple[int | None, int]:
    hub, problem = _capture_hub(c, args.capture_device)
    if hub is None:
        return None, ctx_error(problem + "\n  抓包需要管理员终端（与 `omni usb capture start` 一致）。")
    started = c.post("/api/usb/capture/start", {"device_id": hub})
    if not started.get("success", True):
        return None, _failure(started, "启动抓包失败（--capture）")
    # 抓包启动后再取游标：只统计本次请求产生的传输
    cursor = int(c.get("/api/usb/transfers?limit=1").get("nextId") or 0)
    return cursor, 0


def _capture_end(c: ApiClient, cursor: int) -> list[dict]:
    c.post("/api/usb/capture/stop")
    time.sleep(0.2)
    page = c.get(f"/api/usb/transfers?after_id={cursor}&limit=2000")
    return page.get("items") or []


def _capture_summary(rows: list[dict], endpoints: set[int]) -> str:
    matched = [
        row
        for row in rows
        if int(row.get("endpoint", -1)) in endpoints
        and row.get("typeLabel") in ("control", "bulk", "interrupt")
    ]
    if not matched:
        return "抓包: 没有匹配的传输（确认 hub 选对、抓包在请求前已启动）。"
    sample = "；".join(
        f"#{row.get('id')} {row.get('typeLabel')} "
        f"{str(row.get('direction', '')).upper()} {row.get('length')}B"
        for row in matched[:3]
    )
    return f"抓包: 匹配 {len(matched)} 条（如 {sample}）"


def _winusb_control(c: ApiClient, args):
    try:
        request_type = _request_type(args)
        request = _as_number(args.request, "--request")
        value = _as_number(args.value, "--value")
        index = _as_number(args.index, "--index")
        length = _as_number(args.length, "--length")
    except ValueError as exc:
        return ctx_error(str(exc))
    payload, problem = _read_payload(args)
    if problem:
        return ctx_error(problem)
    if request_type & 0x80 == 0 and length and len(payload or b"") != length:
        return ctx_error("OUT 传输的 --length 必须与数据长度一致（或省略 --length）")

    cursor = None
    if args.capture:
        cursor, code = _capture_begin(c, args)
        if cursor is None:
            return code
    try:
        data = c.post(
            "/api/usb/winusb/control",
            {
                "id": args.id,
                "request_type": request_type,
                "request": request,
                "value": value,
                "index": index,
                "length": length or len(payload or b""),
                "hex": (payload or b"").hex().upper(),
            },
        )
    finally:
        rows = _capture_end(c, cursor) if cursor is not None else []

    if not data.get("success", True):
        return _failure(data, "控制传输失败")
    raw = bytes.fromhex((data.get("dataHex") or "").replace(" ", ""))
    text = hexdump(raw) if raw else "（无数据段）"
    if cursor is not None:
        text += "\n" + _capture_summary(rows, {0x00, 0x80})
    if args.json_:
        if cursor is not None:
            data["capture"] = {"transfers": rows}
        finish(args, data, "")
    else:
        finish(args, data, text)
    return 0


def _winusb_read(c: ApiClient, args):
    if args.count < 0:
        return ctx_error("--count 不能为负数")
    try:
        pipe = _as_number(args.pipe, "--pipe")
    except ValueError as exc:
        return ctx_error(str(exc))
    out_file = None
    if args.out:
        try:
            out_file = open(args.out, "ab")
        except OSError as exc:
            return ctx_error(f"无法写入 {args.out}：{exc}")
    reports: list[dict] = []
    try:
        for index in range(args.count):
            data = c.post(
                "/api/usb/winusb/transfer",
                {
                    "id": args.id,
                    "pipe": pipe,
                    "direction": "in",
                    "length": args.length,
                },
            )
            if not data.get("success", True):
                return _failure(data, f"第 {index + 1} 次读取失败")
            raw = bytes.fromhex((data.get("dataHex") or "").replace(" ", ""))
            if out_file is not None:
                out_file.write(raw)
                out_file.flush()
            reports.append(data)
            if not args.json_ and not args.quiet:
                print(f"#{index + 1}  {len(raw)} 字节")
                if raw:
                    print(hexdump(raw))
    finally:
        if out_file is not None:
            out_file.close()
    if args.json_:
        finish(args, {"reports": reports}, "")
    return 0


def _winusb_write(c: ApiClient, args):
    try:
        pipe = _as_number(args.pipe, "--pipe")
    except ValueError as exc:
        return ctx_error(str(exc))
    payload, problem = _read_payload(args)
    if problem:
        return ctx_error(problem)
    if not payload:
        return ctx_error("需要 --data（十六进制）或 --file")
    written = 0
    for _ in range(max(1, args.repeat)):
        data = c.post(
            "/api/usb/winusb/transfer",
            {
                "id": args.id,
                "pipe": pipe,
                "direction": "out",
                "hex": payload.hex().upper(),
            },
        )
        if not data.get("success", True):
            return _failure(data, "写传输失败")
        written += int(data.get("bytesWritten") or 0)
    finish(
        args,
        {"bytesWritten": written, "repeat": max(1, args.repeat)},
        f"已写 {written} 字节（{max(1, args.repeat)} 次，端点 0x{pipe:02X}）",
    )
    return 0


def _winusb_pipe(c: ApiClient, args):
    try:
        pipe = _as_number(args.pipe, "--pipe")
    except ValueError as exc:
        return ctx_error(str(exc))
    action = "abort" if args.abort else ("flush" if args.flush else "reset")
    data = c.post(
        "/api/usb/winusb/pipe",
        {"id": args.id, "pipe": pipe, "action": action},
    )
    if not data.get("success", True):
        return _failure(data, f"{action} 失败")
    finish(args, data, f"管道 0x{pipe:02X} {action} 完成")
    return 0


def _winusb_echo(c: ApiClient, args):
    try:
        out_pipe = _as_number(args.out_pipe, "--out-pipe")
        in_pipe = _as_number(args.in_pipe, "--in-pipe")
    except ValueError as exc:
        return ctx_error(str(exc))
    cursor = None
    if args.capture:
        cursor, code = _capture_begin(c, args)
        if cursor is None:
            return code
    try:
        data = c.post(
            "/api/usb/winusb/echo",
            {
                "id": args.id,
                "out_pipe": out_pipe,
                "in_pipe": in_pipe,
                "size": args.size,
                "count": args.count,
                "pipeline": args.pipeline,
                "pattern": args.pattern,
                "seed": args.seed or 0,
            },
        )
    finally:
        rows = _capture_end(c, cursor) if cursor is not None else []

    if not data.get("success", True):
        return _failure(data, "回环测试失败")
    capture_line = (
        "\n" + _capture_summary(rows, {out_pipe, in_pipe}) if cursor is not None else ""
    )
    if args.json_:
        if cursor is not None:
            data["capture"] = {"transfers": rows}
        finish(args, data, "")
        return 0 if not data.get("mismatches") else 1
    finish(
        args,
        data,
        f"echo 0x{out_pipe:02X} -> 0x{in_pipe:02X}: {data.get('iterations')} 次 × "
        f"{data.get('size')} 字节（pipeline {data.get('pipeline')}）\n"
        f"吞吐 {data.get('throughputKiBs')} KiB/s，不一致 {data.get('mismatches')} 次"
        + (f"\n首个错误: {data['firstError']}" if data.get("firstError") else "")
        + capture_line,
    )
    return 0 if not data.get("mismatches") else 1


def add_parser(sub):
    p = sub.add_parser(
        "usb",
        help="USB 描述符 / 请求 / 抓包 / 测试（bus 服务）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "USB 开发工作台：枚举设备、发控制/批量请求、生成与校验描述符、"
            "用 USBPcap 抓包分析、跑回归用例。"
        ),
        epilog=(
            "Examples:\n"
            "  omni usb doctor\n"
            "  omni usb list\n"
            "  omni usb capture start --devices 1,2\n"
            "  omni usb capture transfers --follow\n"
            "  omni usb capture open --pcap sess.pcap --json\n"
            "  omni usb desc parse --hex \"80 06 00 01 00 00 12 00\"\n"
            "  omni usb winusb list\n"
            "  omni usb winusb open --vid 0xC251 --pid 0x0002\n"
            "  omni usb winusb echo --out-pipe 0x01 --in-pipe 0x81 --size 512\n"
            "  omni usb hid list\n"
            "  omni usb cdc list\n"
            "  omni usb test run --file case.json --json\n"
            "\n"
            "抓包需要安装 USBPcap 并以管理员身份运行；描述符与请求功能不需要。"
        ),
    )
    ps = p.add_subparsers(dest="usb_sub", metavar="<sub>")

    # ── 环境诊断 ──────────────────────────────────────────
    s = ps.add_parser("doctor", parents=[common_parser()], help="环境诊断（USBPcap / 管理员 / WinUSB）")
    s.set_defaults(func=run_command(_doctor))

    # ── 设备列表 ──────────────────────────────────────────
    s = ps.add_parser("list", parents=[common_parser()], help="列出系统 USB 设备（VID/PID、驱动、hub、动态地址）")
    s.add_argument("--vid", help="按 VID 过滤，如 0xC251")
    s.add_argument("--pid", help="按 PID 过滤，如 0x0002")
    s.add_argument("--driver", help="按驱动服务过滤，如 WINUSB / usbser / HidUsb")
    s.add_argument("--all", action="store_true", help="按 root hub 分组显示所有设备（树形）")
    s.set_defaults(func=run_command(_usb_list))

    # ── 描述符（M2） ──────────────────────────────────────
    d = ps.add_parser("desc", help="描述符解析 / 校验 / 生成 / 模板")
    ds = d.add_subparsers(dest="usb_desc_sub", metavar="<sub>")

    s = ds.add_parser("parse", parents=[common_parser()], help="解析描述符或 Setup 包")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex", help="十六进制字节，如 \"12 01 00 02 ...\"")
    g.add_argument("--file", help="从文件读取原始字节")
    g.add_argument("--stdin", action="store_true", help="从标准输入读取十六进制")
    s.set_defaults(func=run_command(_desc_parse))

    s = ds.add_parser("validate", parents=[common_parser()], help="校验描述符文档（JSON）")
    s.add_argument("--file", required=True, help="描述符文档路径")
    s.set_defaults(func=run_command(_desc_validate))

    s = ds.add_parser("build", parents=[common_parser()], help="由 JSON 描述生成 hex")
    s.add_argument("--file", required=True, help="描述符文档路径")
    s.add_argument("--out", metavar="FILE", help="输出文件（JSON 结果）")
    s.set_defaults(func=run_command(_desc_build))

    s = ds.add_parser("emit", parents=[common_parser()], help="导出描述符（hex / json）")
    s.add_argument("--file", required=True, help="描述符文档路径")
    s.add_argument("--format", choices=("hex", "json"), default="hex")
    s.add_argument("--out", metavar="FILE")
    s.set_defaults(func=run_command(_desc_emit))

    s = ds.add_parser("from-capture", parents=[common_parser()], help="从离线 pcap 反推描述符文档")
    s.add_argument("--pcap", required=True, metavar="FILE", help="pcap 文件")
    s.add_argument("--bus", type=int)
    s.add_argument("--device", type=int)
    s.set_defaults(func=run_command(_desc_from_capture))

    t = ds.add_parser("template", help="内置描述符模板")
    ts = t.add_subparsers(dest="usb_template_sub", metavar="<sub>")
    s = ts.add_parser("list", parents=[common_parser()], help="列出模板")
    s.set_defaults(func=run_command(_desc_template_list))
    s = ts.add_parser("show", parents=[common_parser()], help="显示模板文档")
    s.add_argument("name", help="模板名，如 cdc-acm")
    s.set_defaults(func=run_command(_desc_template_show))

    # ── 抓包（M1） ────────────────────────────────────────
    c = ps.add_parser("capture", help="USBPcap 实时抓包 / 离线解析")
    cs = c.add_subparsers(dest="usb_capture_sub", metavar="<sub>")

    s = cs.add_parser("bind", parents=[common_parser()], help="绑定默认 root hub（之后 start 不用 --device）")
    s.add_argument("--device", help="root hub，如 USBPcap1（省略时自动选择唯一的一个）")
    s.set_defaults(func=run_command(_capture_bind))

    s = cs.add_parser("unbind", parents=[common_parser()], help="取消 root hub 绑定")
    s.set_defaults(func=run_command(_capture_unbind))

    s = cs.add_parser("start", parents=[common_parser()], help="开始抓包（需要管理员）")
    s.add_argument("--device", help="root hub，如 \\\\.\\USBPcap1（省略时用 capture bind 的绑定，或唯一时自动选择）")
    s.add_argument("--snaplen", type=int, help="快照长度（默认由后端决定）")
    s.add_argument("--bufferlen", type=int, help="USBPcap 内部缓冲（4096..134217728）")
    s.add_argument("--devices", help="只抓指定设备地址，如 1,2,3")
    s.add_argument("--usbpcap-path", metavar="PATH", help="USBPcapCMD.exe 路径")
    s.set_defaults(func=run_command(_capture_start))

    s = cs.add_parser("stop", parents=[common_parser()], help="停止抓包（幂等）")
    s.set_defaults(func=run_command(_capture_stop))

    s = cs.add_parser("status", parents=[common_parser()], help="抓包状态与统计")
    s.set_defaults(func=run_command(_capture_status))

    s = cs.add_parser("transfers", parents=[common_parser()], help="查询传输（增量 / 持续跟踪）")
    s.add_argument("--since", type=int, metavar="ID", help="从该传输 ID 之后开始")
    s.add_argument("--follow", action="store_true", help="持续跟踪（Ctrl+C 结束）")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--interval", type=float, default=0.3, help="follow 轮询间隔秒数（默认 0.3）")
    s.add_argument("--duration", type=float, default=0.0, help="follow 持续秒数（默认 0=一直）")
    s.add_argument("--type", help="control,bulk,interrupt,isochronous")
    s.add_argument("--dir", choices=("in", "out"))
    s.add_argument("--bus", type=int, help="总线号（表格里 1.7 的 1）")
    s.add_argument(
        "--device",
        type=_device_address,
        metavar="ADDR",
        help="设备地址（表格里 1.7 的 7；root hub 在 capture start 时选）",
    )
    s.add_argument("--endpoint", type=_endpoint_address, help="端点地址，如 0x81")
    s.add_argument("--status", help="ok / error")
    s.add_argument("--vid", help="按设备 VID 过滤，如 0xC251（用抓包里的设备描述符）")
    s.add_argument("--pid", help="按设备 PID 过滤，如 0x0002")
    s.add_argument("--q", help="摘要/设备号文本搜索")
    s.add_argument(
        "--data-bytes",
        type=int,
        default=64,
        metavar="N",
        help="数据列显示的字节数（默认 64，0=不显示；超出部分用 … 标注）",
    )
    s.add_argument(
        "--no-setup",
        action="store_true",
        help="不拆出 CTL(SETUP) 行，控制传输只显示数据阶段",
    )
    s.add_argument(
        "--data-per-line",
        type=int,
        choices=(4, 8, 16),
        default=8,
        metavar="N",
        help="数据列每行字节数（默认 8；窗口过窄时自动降到 4）",
    )
    s.set_defaults(func=run_command(_capture_transfers))

    s = cs.add_parser("detail", parents=[common_parser()], help="单条传输详情")
    s.add_argument("transfer_id", type=int, help="传输 ID")
    s.set_defaults(func=run_command(_capture_detail))

    s = cs.add_parser("devices", parents=[common_parser()], help="从抓包重建的设备树")
    s.set_defaults(func=run_command(_capture_devices))

    s = cs.add_parser("open", parents=[common_parser()], help="离线打开 pcap（无需 USBPcap）")
    s.add_argument("--pcap", required=True, metavar="FILE")
    s.add_argument("--no-wait", action="store_true", help="不等待解析完成（配合 --json）")
    s.set_defaults(func=run_command(_capture_open))

    s = cs.add_parser("save", parents=[common_parser()], help="导出当前抓包文件")
    s.add_argument("--out", required=True, metavar="FILE")
    s.set_defaults(func=run_command(_capture_save))

    # ── WinUSB（控制/批量/中断 + echo） ──────────────────
    w = ps.add_parser("winusb", help="WinUSB 控制/批量/中断传输（设备需绑定 WinUSB）")
    ws = w.add_subparsers(dest="usb_winusb_sub", metavar="<sub>")

    s = ws.add_parser("list", parents=[common_parser()], help="列出 WinUSB 接口（--all 含全部 USB 设备）")
    s.add_argument("--guid", action="append", metavar="GUID", help="设备接口 GUID（可重复）")
    s.add_argument("--all", action="store_true", help="列出所有 USB 设备及驱动服务名（等价 omni usb list）")
    s.set_defaults(func=run_command(_winusb_list))

    s = ws.add_parser("info", parents=[common_parser()], help="设备/接口/端点详情")
    s.add_argument("--index", type=int, help="设备序号（list 输出）")
    s.add_argument("--vid")
    s.add_argument("--pid")
    s.add_argument("--guid", action="append", metavar="GUID")
    s.set_defaults(func=run_command(_winusb_info))

    s = ws.add_parser("open", parents=[common_parser()], help="打开 WinUSB 接口（独占）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--path", help="设备接口路径（优先于 VID/PID）")
    s.add_argument("--vid")
    s.add_argument("--pid")
    s.add_argument("--interface", type=int, default=0)
    s.add_argument("--timeout-ms", type=int, default=1000)
    s.set_defaults(func=run_command(_winusb_open))

    s = ws.add_parser("close", parents=[common_parser()], help="关闭会话")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_winusb_close))

    s = ws.add_parser("status", parents=[common_parser()], help="查询会话状态")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_winusb_status))

    s = ws.add_parser("control", parents=[common_parser()], help="控制传输（SETUP + 数据段）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--request-type", help="bmRequestType（优先于方向/类型/接收者）")
    s.add_argument("--direction", choices=("in", "out"), default="in")
    s.add_argument("--type", dest="req_type", choices=("standard", "class", "vendor"), default="standard")
    s.add_argument("--recipient", choices=("device", "interface", "endpoint", "other"), default="device")
    s.add_argument("--request", required=True, help="bRequest，如 0x06")
    s.add_argument("--value", default="0")
    s.add_argument("--index", default="0")
    s.add_argument("--length", default="0")
    s.add_argument("--data", help="OUT 数据（十六进制）")
    s.add_argument("--file", help="OUT 数据文件")
    s.add_argument("--capture", action="store_true", help="同时用 USBPcap 抓包并核对传输")
    s.add_argument("--capture-device", help="抓包 root hub（多个时必填）")
    s.set_defaults(func=run_command(_winusb_control))

    s = ws.add_parser("read", parents=[common_parser()], help="IN 传输（批量/中断）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--pipe", required=True, help="端点地址，如 0x81")
    s.add_argument("--length", type=int, required=True, help="单次读取字节数")
    s.add_argument("--count", type=int, default=1, help="读取次数")
    s.add_argument("--out", metavar="FILE", help="追加写入文件")
    s.set_defaults(func=run_command(_winusb_read))

    s = ws.add_parser("write", parents=[common_parser()], help="OUT 传输（批量/中断）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--pipe", required=True, help="端点地址，如 0x01")
    s.add_argument("--data", help="数据（十六进制）")
    s.add_argument("--file", help="数据文件")
    s.add_argument("--repeat", type=int, default=1)
    s.set_defaults(func=run_command(_winusb_write))

    s = ws.add_parser("pipe", parents=[common_parser()], help="管道控制（abort/flush/reset）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--pipe", required=True, help="端点地址")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--abort", action="store_true")
    g.add_argument("--flush", action="store_true")
    g.add_argument("--reset", action="store_true")
    s.set_defaults(func=run_command(_winusb_pipe))

    s = ws.add_parser("echo", parents=[common_parser()], help="回环吞吐测试")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--out-pipe", required=True)
    s.add_argument("--in-pipe", required=True)
    s.add_argument("--size", type=int, default=64)
    s.add_argument("--count", type=int, default=100, help="迭代次数（0=一直）")
    s.add_argument("--pipeline", type=int, default=1, help="连续写 N 次再读回")
    s.add_argument("--pattern", choices=("increment", "random", "zeros", "aa", "ff"), default="increment")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--capture", action="store_true", help="同时用 USBPcap 抓包并核对传输")
    s.add_argument("--capture-device", help="抓包 root hub（多个时必填）")
    s.set_defaults(func=run_command(_winusb_echo))

    # ── USB HID（系统 HID 驱动，报告级） ─────────────────
    h = ps.add_parser("hid", help="USB HID 报告收发（系统 HID 驱动）")
    hs = h.add_subparsers(dest="usb_hid_sub", metavar="<sub>")

    s = hs.add_parser("list", parents=[common_parser()], help="列出 HID 设备")
    s.add_argument("--vid", help="按 VID 过滤，如 0xC251")
    s.add_argument("--pid", help="按 PID 过滤，如 0x0001")
    s.set_defaults(func=run_command(_hid_list))

    s = hs.add_parser("open", parents=[common_parser()], help="打开 HID 设备（独占）")
    s.add_argument("--vid", required=True, help="VID，如 0xC251")
    s.add_argument("--pid", required=True, help="PID，如 0x0001")
    s.add_argument("--serial", help="序列号（同型号多设备时区分）")
    s.add_argument("--path", help="设备接口路径（优先于 VID/PID）")
    s.add_argument("--id", default="cli", metavar="ID", help="会话 ID")
    s.add_argument("--timeout-ms", type=int, default=1000, help="报告收发超时（默认 1000）")
    s.set_defaults(func=run_command(_hid_open))

    s = hs.add_parser("read", parents=[common_parser()], help="读取 Input 报告")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--length", type=int, help="报告长度（默认取设备 InputReportByteLength）")
    s.add_argument("--count", type=int, default=1, help="读取次数（默认 1）")
    s.add_argument("--timeout-ms", type=int, default=None, help="单次超时")
    s.add_argument("--out", metavar="FILE", help="把报告追加写入文件（二进制）")
    s.set_defaults(func=run_command(_hid_read))

    s = hs.add_parser("write", parents=[common_parser()], help="写 Output 报告")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--data", help="报告载荷（十六进制，不含 Report ID）")
    s.add_argument("--file", help="从文件读取载荷")
    s.add_argument("--report-id", type=int, default=0, help="Report ID（默认 0）")
    s.add_argument("--timeout-ms", type=int, default=None)
    s.set_defaults(func=run_command(_hid_write))

    s = hs.add_parser("feature", parents=[common_parser()], help="读写 Feature 报告")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--report-id", type=int, default=0)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--get", action="store_true", help="读取 Feature 报告")
    g.add_argument("--set", action="store_true", help="写入 Feature 报告")
    s.add_argument("--data", help="载荷（十六进制）")
    s.add_argument("--file", help="载荷文件")
    s.set_defaults(func=run_command(_hid_feature))

    s = hs.add_parser("status", parents=[common_parser()], help="查询 HID 会话状态")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_hid_status))

    s = hs.add_parser("close", parents=[common_parser()], help="关闭 HID 会话")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_hid_close))

    # ── USB CDC（复用串口后端） ──────────────────────────
    c = ps.add_parser("cdc", help="USB CDC 收发（复用串口后端；含 BREAK）")
    cs = c.add_subparsers(dest="usb_cdc_sub", metavar="<sub>")

    s = cs.add_parser("list", parents=[common_parser()], help="列出 USB CDC 设备（按 VID/PID 过滤）")
    s.set_defaults(func=run_command(_cdc_list))

    s = cs.add_parser("open", parents=[common_parser()], help="打开 CDC 串口")
    s.add_argument("--port", required=True, metavar="COM", help="端口名，如 COM65")
    s.add_argument("--baud", type=int, default=115200, help="波特率（默认 115200）")
    s.add_argument("--bytesize", type=int, default=8, choices=(5, 6, 7, 8))
    s.add_argument("--parity", default="N", choices=("N", "E", "O", "M", "S"))
    s.add_argument("--stopbits", type=float, default=1, choices=(1, 1.5, 2))
    s.add_argument("--flow", default="none", choices=("none", "xonxoff", "rtscts"))
    s.add_argument("--rts", action="store_true", help="打开时置位 RTS")
    s.add_argument("--dtr", action="store_true", help="打开时置位 DTR")
    s.add_argument("--id", default="cli", metavar="ID", help="连接实例 ID")
    s.set_defaults(func=run_command(_cdc_open))

    s = cs.add_parser("send", parents=[common_parser()], help="发送数据（文本 / 十六进制 / 文件）")
    s.add_argument("--text", metavar="TEXT", help="发送文本（默认追加换行）")
    s.add_argument("--hex", metavar="HEX", help="发送十六进制字节")
    s.add_argument("--file", metavar="PATH", help="发送文件原始字节")
    s.add_argument("--no-newline", action="store_true", help="--text 不追加换行")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_cdc_send))

    s = cs.add_parser("read", parents=[common_parser()], help="读取接收缓冲（一次性 / 同步等读 / 持续跟踪）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.add_argument("--since", type=int, default=None, help="从指定 seq 开始读")
    s.add_argument("--max-bytes", type=int, default=65536)
    s.add_argument("--wait", type=int, default=None, metavar="MS", help="同步等读，最多等该毫秒数")
    s.add_argument("--follow", action="store_true", help="持续跟踪（Ctrl+C 结束）")
    s.add_argument("--interval", type=float, default=0.2)
    s.add_argument("--duration", type=float, default=0.0)
    s.add_argument("--out", metavar="FILE", help="原始字节追加写入文件")
    s.add_argument("--encoding", default=None, metavar="ENC")
    s.add_argument("--hex", action="store_true", help="十六进制转储显示")
    s.set_defaults(func=run_command(_cdc_read))

    s = cs.add_parser("break", parents=[common_parser()], help="发送 BREAK（CDC SEND_BREAK 等价）")
    s.add_argument("--duration-ms", type=int, default=250, help="BREAK 持续时间（默认 250 ms）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_cdc_break))

    s = cs.add_parser("status", parents=[common_parser()], help="查询 CDC 状态与接收缓冲")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_cdc_status))

    s = cs.add_parser("close", parents=[common_parser()], help="关闭 CDC（接收缓存保留）")
    s.add_argument("--id", default="cli", metavar="ID")
    s.set_defaults(func=run_command(_cdc_close))

    # ── 测试（M4） ────────────────────────────────────────
    t = ps.add_parser("test", help="声明式回归用例")
    ts = t.add_subparsers(dest="usb_test_sub", metavar="<sub>")

    s = ts.add_parser("run", parents=[common_parser()], help="执行用例")
    s.add_argument("--file", required=True, metavar="FILE", help="用例 JSON")
    s.add_argument("--junit", metavar="FILE", help="输出 JUnit XML")
    s.add_argument("--keep-going", action="store_true", help="失败后继续剩余步骤")
    s.set_defaults(func=run_command(_test_run))

    s = ts.add_parser("list", parents=[common_parser()], help="列出内置用例")
    s.set_defaults(func=run_command(_test_list))
