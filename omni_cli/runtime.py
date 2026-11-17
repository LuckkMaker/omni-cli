"""叶子命令运行时：后端连接解析、统一输出与退出码。

独立于 cli.py，避免命令模块与分发器循环导入。

多服务要点：

- **命令组 → 服务**：总线类命令组（serial / net / modbus / usb）走 ``bus``，
  其余走 ``link``；叶子命令可用 ``--service link|bus`` 覆盖。
- **状态型命令拒绝 --managed**：``* status`` 这类只查询的命令如果用临时托管后端，
  查到的永远是空状态（临时后端没有会话），因此直接拒绝并提示先 ``backend start``。
"""

import json
import sys

from . import client, settings
from .formatting import ctx_error

#: 命令组 → 后端服务。未列出的命令组默认走 link。
COMMAND_SERVICE = {
    "serial": settings.SERVICE_BUS,
    "net": settings.SERVICE_BUS,
    "network": settings.SERVICE_BUS,
    "modbus": settings.SERVICE_BUS,
    "usb": settings.SERVICE_BUS,
}


def service_for(args) -> str:
    """解析一条命令应该连哪个后端服务。

    优先级：``--service`` 显式覆盖 > 命令组映射 > 默认 link。
    """
    explicit = getattr(args, "service", None)
    if explicit:
        return settings.service(explicit).name
    return COMMAND_SERVICE.get(getattr(args, "command", None), settings.DEFAULT_SERVICE)


def leaf_subcommand(args) -> str:
    """取叶子子命令名（各模块的 subparser 都叫 ``*_sub``）。"""
    for key, value in vars(args).items():
        if key.endswith("_sub") and isinstance(value, str):
            return value
    return ""


def reject_managed(args) -> str | None:
    """状态型命令（``* status``）不允许 ``--managed``，返回错误文案或 None。"""
    if not getattr(args, "managed", False):
        return None
    if leaf_subcommand(args) == "status":
        svc = service_for(args)
        return (
            "状态查询命令不支持 --managed：临时托管后端没有会话，查到的永远是空状态。\n"
            f"  请先执行 `omni backend start {svc}` 启动常驻后端再查询。"
        )
    return None


def _warn_protocol(info: dict) -> None:
    """后端协议比本 CLI 新时提示一次（不阻断，兼容窗口内仍可用）。"""
    proto = info.get("protocol")
    if isinstance(proto, int) and proto > client.BACKEND_PROTOCOL:
        print(
            f"警告: 后端协议 {proto} 高于本 CLI 支持的最高版本 {client.BACKEND_PROTOCOL}，"
            "部分命令可能不可用；建议升级 OMNI CLI。",
            file=sys.stderr,
        )


def run_command(handler):
    """包装叶子命令：建立后端客户端并执行，托管模式结束自动关停。"""

    def _run(args):
        problem = reject_managed(args)
        if problem:
            return ctx_error(problem)
        svc = service_for(args)
        c, cleanup, hung_ports = _resolve(args, svc)
        if c is None:
            if hung_ports:
                ports = ", ".join(str(p) for p in hung_ports)
                return ctx_error(
                    f"后端（服务={svc}）在端口 {ports} 上有监听但 HTTP 无响应"
                    "（进程可能已僵死，或正被挂起的硬件操作阻塞）。\n"
                    f"  请执行 `omni backend stop {svc}` 停止后重新 `omni backend start {svc}`。"
                )
            return ctx_error(
                f"未找到正在运行的后端（服务={svc}）。请先执行 `omni backend start {svc}` 启动常驻后端，"
                "或加 --managed 由本命令临时拉起。"
            )
        try:
            return handler(c, args) or 0
        finally:
            if cleanup:
                cleanup()

    return _run


def _resolve(args, service_name: str = settings.DEFAULT_SERVICE):
    """返回 (client, cleanup, hung_ports)。

    - managed：临时拉起；
    - 否则附加到第一个“可达”端口；
    - 全部不可达时，记录“端口在监听但 HTTP 不通”的端口（僵死后端），
      交给调用方给出可执行的提示，而不是误报“后端未启动”。
    """
    if args.managed:
        proc, port = client.start_managed(args.timeout, service_name)
        return client.ApiClient(port, args.timeout), lambda: client.stop(proc.pid), []

    ports = []
    if args.backend_port:
        ports.append(args.backend_port)
    ports += settings.candidate_ports(service_name)

    hung: list[int] = []
    for p in ports:
        info = client.health_info(p)
        if info is not None:
            _warn_protocol(info)
            return client.ApiClient(p, args.timeout), None, []
        # 健康检查失败：区分“没进程”（连接被拒）与“进程僵死”（TCP 可连但 HTTP 超时）
        if client.port_open(p):
            hung.append(p)
    return None, None, hung


def run_local(handler):
    """包装本地命令：不解析后端连接，仅统一异常与退出码。

    与 ``run_command`` 的区别只在第一步——不建 ``ApiClient``，因此后端没启动
    也能执行。用于只读本地文件/配置的命令（如 ``file checksum``、``config *``）。
    """

    def _run(args):
        try:
            return handler(args) or 0
        except KeyboardInterrupt:
            return ctx_error("已中断")
        except OSError as e:
            return ctx_error(f"文件/进程操作失败: {e}")
        except Exception as e:  # 本地命令不把 traceback 抛给终端用户
            return ctx_error(f"{type(e).__name__}: {e}")

    return _run


def finish(args, raw, human):
    """统一输出：json 模式打印 raw 对象，否则打印 human 文本。"""
    if args.quiet:
        return
    if args.json_:
        print(json.dumps(raw, ensure_ascii=False, indent=2))
    else:
        print(human)
