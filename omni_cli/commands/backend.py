"""后端生命周期命令：backend start / stop / status / list。

多服务（link / bus）：

- ``omni backend start [link|bus]``：已有可达后端则复用，否则拉起该服务的常驻后端。
  位置参数默认 ``link``（向后兼容）。
- ``omni backend stop [link|bus]``：先优雅关停，失败再按端口定位进程树强杀。
- ``omni backend status [link|bus]``：查该服务；不带位置参数时同时给出另一个服务的汇总。
- ``omni backend list``：列出所有已知服务的运行状态（等价于 status 的横向视图）。
"""

import time

from .. import __version__ as CLI_VERSION
from .. import client as bclient
from .. import settings
from ..formatting import ctx_error
from ..options import common_parser
from ..runtime import finish

SERVICE_CHOICES = settings.service_names()


def add_parser(sub):
    bp = sub.add_parser("backend", help="后端生命周期管理（link / bus）")
    bsub = bp.add_subparsers(dest="backend_sub", metavar="<sub>")

    sp = bsub.add_parser("start", parents=[common_parser(include_service=False)], help="启动常驻后端进程")
    sp.add_argument("service", nargs="?", default=None, choices=SERVICE_CHOICES, metavar="SERVICE",
                    help="后端服务（默认 link）")
    sp.set_defaults(func=_start)

    sp = bsub.add_parser("stop", parents=[common_parser(include_service=False)], help="停止常驻后端")
    sp.add_argument("service", nargs="?", default=None, choices=SERVICE_CHOICES, metavar="SERVICE",
                    help="后端服务（默认 link）")
    sp.set_defaults(func=_stop)

    sp = bsub.add_parser("status", parents=[common_parser(include_service=False)], help="查询后端运行状态")
    sp.add_argument("service", nargs="?", default=None, choices=SERVICE_CHOICES, metavar="SERVICE",
                    help="后端服务（默认 link；不给时同时输出另一服务的汇总）")
    sp.set_defaults(func=_status)

    sp = bsub.add_parser("list", parents=[common_parser(include_service=False)], help="列出所有服务的运行状态")
    sp.set_defaults(func=_list)


def _target(args) -> str:
    """命令要操作的服务名（位置参数优先）。"""
    return settings.service(getattr(args, "service", None)).name


def _reject_managed(args):
    """backend 子命令自己就是生命周期管理，--managed 无意义。"""
    if getattr(args, "managed", False):
        return ctx_error("backend 命令本身就是后端生命周期管理，不支持 --managed")
    return None


def _ports_for(name: str, args) -> list[int]:
    ports: list[int] = []
    if args.backend_port:
        ports.append(args.backend_port)
    ports += settings.candidate_ports(name)
    return ports


def _start(args):
    problem = _reject_managed(args)
    if problem:
        return problem
    name = _target(args)

    # 已有可达后端（含应用托管 / 共享注册表发现的后端）则直接复用，
    # 否则会再拉起一个后端去抢同一设备。
    for p in _ports_for(name, args):
        if p and bclient.reachable(p):
            finish(args, {"running": True, "reused": True, "service": name, "port": p},
                   f"后端（{name}）已在运行: 端口={p}（复用现有后端，未重复启动）")
            return 0

    info = bclient.start_persistent(args.timeout, name)
    human = f"后端（{name}）已启动: pid={info['pid']} 端口={info['port']}"
    finish(args, {"running": True, "service": name, "pid": info["pid"], "port": info["port"]}, human)
    return 0


def _stop(args):
    problem = _reject_managed(args)
    if problem:
        return problem
    name = _target(args)
    rt = settings.read_runtime(name)
    pid = rt.get("pid") if rt else None
    ports = _ports_for(name, args)

    # 关停前快照监听进程。必须按 PID 判活而不是看端口：uvicorn 优雅退出会先关闭
    # 监听套接字、再跑 lifespan 清理，清理若被硬件操作卡住则进程仍活着占着设备——
    # 只看端口会把「还在跑」误判成「已停止」。
    listeners = _listener_pids(ports)

    # 1) 优雅关停：仅对能响应 HTTP 的后端有效。事件循环被 J-Link 等同步调用阻塞时
    #    /api/health 恒超时（僵死），走不了优雅路径，直接落到强杀。
    for p in ports:
        if p and bclient.reachable(p):
            try:
                bclient.ApiClient(p, args.timeout).post("/api/system/shutdown")
            except Exception:
                # 服务端在响应后立即退出，连接中断属正常现象
                pass
            # 等后端自身退出：其关停看门狗宽限期 10s（common/omni_service.install_shutdown），
            # 这里多等几秒即可拿到干净结果，不必抢在它前面强杀。
            if _wait_pids_gone(listeners, timeout=14.0):
                settings.clear_runtime(name)
                pid_txt = f"  pid={pid}" if pid else ""
                finish(args, {"stopped": True, "service": name, "port": p, "pid": pid},
                       f"后端（{name}）已停止: 端口={p}{pid_txt}")
                return 0
            break

    # 2) 强杀：按端口定位真实监听进程，不依赖运行时记录——手动 `server.py` /
    #    Electron 托管的后端没有 pid 记录，只有端口能找到它。stop() 连子树一起杀，
    #    避免 venv 启动器留下的子进程继续持有设备句柄。
    #    无监听进程时才回退到运行时 pid（后端存活但没在监听的场景）。
    victims = _listener_pids(ports)
    if not victims and pid and bclient.pid_alive(pid):
        victims = [int(pid)]
    if not victims:
        return ctx_error(f"没有正在运行的后端（服务={name}；端口不可达，且未定位到监听进程）")

    for v in victims:
        bclient.stop(v)

    # 3) 校验进程真的消失（僵死进程占着监听套接字，不确认就可能漏杀）
    if not _wait_pids_gone(victims, timeout=6.0):
        settings.clear_runtime(name)
        return ctx_error(
            f"后端进程 {victims}（服务={name}）未能停止（可能需管理员权限）"
        )
    settings.clear_runtime(name)
    finish(args, {"stopped": True, "service": name, "pids": victims},
           f"后端（{name}）已停止: pid={','.join(str(v) for v in victims)}")
    return 0


def _listener_pids(ports) -> list[int]:
    """汇总候选端口上的监听进程 PID（去重）。"""
    pids: set[int] = set()
    for p in ports:
        if p:
            pids |= set(bclient.find_pids_by_port(p))
    return sorted(pids)


def _wait_pids_gone(pids, timeout: float = 8.0) -> bool:
    """等待给定 PID 全部退出，确认后端进程消失、设备等资源已释放。空集合视为已完成。"""
    pids = [p for p in pids if p]
    if not pids:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(bclient.pid_alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


def _probe(name: str, args, with_backend_port: bool = True) -> dict:
    """探测一个服务的运行状态。

    以端口监听为准：进程存在但 HTTP 不通=僵死（Windows 上 os.kill 不可靠）。
    """
    # pid 优先取 CLI 自己的常驻记录；后端由 GUI/手动拉起时只有共享登记表里有 pid
    rt = settings.read_runtime(name)
    shared = settings.read_shared_runtime(name)
    pid = (rt.get("pid") if rt else None) or (shared.get("pid") if shared else None)

    ports: list[int] = []
    if with_backend_port and args.backend_port:
        ports.append(args.backend_port)
    ports += settings.candidate_ports(name)

    port = None
    hung = False
    health: dict = {}
    for p in ports:
        if not p:
            continue
        info = bclient.health_info(p)
        if info is not None:
            port, health = p, info
            break
        if bclient.port_open(p):
            port, hung = p, True
            break

    return {
        "service": name,
        "running": port is not None,
        "hung": hung,
        "pid": pid,
        "port": port,
        "protocol": health.get("protocol"),
        "version": health.get("version"),
    }


def _status(args):
    problem = _reject_managed(args)
    if problem:
        return problem

    explicit = getattr(args, "service", None)
    primary = settings.service(explicit).name
    # 不给位置参数时同时展示另一个服务（默认仍以 link 的退出码为准，向后兼容）
    others = [n for n in SERVICE_CHOICES if n != primary] if explicit is None else []

    main = _probe(primary, args)
    extra = [_probe(n, args, with_backend_port=False) for n in others]

    alive = bool(main["running"])
    if args.quiet:
        return 0 if alive else 1

    if args.json_:
        import json
        payload = {
            "cliVersion": CLI_VERSION,
            "running": alive,
            "hung": main["hung"],
            "pid": main["pid"],
            "port": main["port"],
            "protocol": main["protocol"],
            "version": main["version"],
            "service": primary,
            "services": [main] + extra,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if alive else 1

    print(f"CLI    omni {CLI_VERSION}")
    print(_format_state(main))
    for item in extra:
        print(_format_state(item))
    return 0 if alive else 1


def _format_state(info: dict) -> str:
    """一行状态文案（link/bus 共用）。"""
    name = info["service"]
    if info["hung"]:
        pid_txt = f"  pid={info['pid']}" if info["pid"] else ""
        return (f"僵死  {name}  端口={info['port']}{pid_txt}"
                f"（HTTP 无响应，用 `omni backend stop {name}` 强杀）")
    if info["running"]:
        pid_txt = f"  pid={info['pid']}" if info["pid"] else ""
        proto_txt = f"  协议={info['protocol']}" if info["protocol"] else ""
        ver_txt = f"  版本={info['version']}" if info["version"] else ""
        return f"运行中  {name}  端口={info['port']}{pid_txt}{proto_txt}{ver_txt}"
    return f"未运行  {name}（可用 `omni backend start {name}` 启动）"


def _list(args):
    problem = _reject_managed(args)
    if problem:
        return problem

    states = [_probe(n, args, with_backend_port=False) for n in SERVICE_CHOICES]
    alive = any(s["running"] for s in states)
    if args.quiet:
        return 0 if alive else 1
    if args.json_:
        import json
        print(json.dumps({"cliVersion": CLI_VERSION, "services": states, "running": alive},
                         ensure_ascii=False, indent=2))
        return 0 if alive else 1
    print(f"CLI    omni {CLI_VERSION}")
    for item in states:
        print(_format_state(item))
    return 0 if alive else 1
