"""多服务（link / bus）离线回归测试（无需硬件、不启动后端进程）。

覆盖：

1. ``settings.services()``：两个服务、各自独立的登记表路径与状态目录环境变量；
2. 环境变量覆盖（``OMNI_STATE_DIR`` / ``OMNI_BUS_STATE_DIR``）；
3. 后端目录解析：``OMNI_BACKEND_DIR`` / ``OMNI_BUS_BACKEND_DIR`` 指向仓库根或服务目录都能认；
4. ``runtime.service_for``：命令组 → 服务映射 + ``--service`` 覆盖；
5. ``runtime.reject_managed``：``* status`` 拒绝 ``--managed``；
6. ``autoinstall``：候选安装目录包含 link / bus 两套，且不重复。

用法::

    python tests/test_multi_service.py
"""

import os
import sys
import tempfile
from pathlib import Path

PY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PY_DIR)

from types import SimpleNamespace  # noqa: E402

from omni_cli import autoinstall, runtime, settings  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    if not cond:
        failures.append(name)
    print(f"  {status}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def _args(**kwargs):
    """构造最小 args（其余字段按叶子命令默认值补齐）。"""
    base = dict(backend_port=None, service=None, managed=False, json_=False,
                quiet=False, timeout=30, command=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── 1. 服务定义 ────────────────────────────────────────────────
def test_services() -> None:
    print("[1] settings.services()")
    services = settings.services()
    check("两个服务：link / bus", set(services) == {"link", "bus"}, str(set(services)))

    link, bus = services["link"], services["bus"]
    check("link 状态环境变量为 OMNI_STATE_DIR", link.state_env == "OMNI_STATE_DIR", link.state_env)
    check("bus 状态环境变量为 OMNI_BUS_STATE_DIR", bus.state_env == "OMNI_BUS_STATE_DIR", bus.state_env)
    check("两个登记表路径不同", link.registry_file() != bus.registry_file(),
          f"{link.registry_file()} vs {bus.registry_file()}")
    check("登记表文件名固定 backend.json",
          all(s.registry_file().endswith("backend.json") for s in services.values()))
    check("bus 的 exe 名与 link 不同", bus.exe_name != link.exe_name,
          f"{bus.exe_name} vs {link.exe_name}")


# ── 2. 状态目录覆盖 ────────────────────────────────────────────
def test_state_env_override() -> None:
    print("[2] 状态目录环境变量覆盖")
    saved = {s.state_env: os.environ.get(s.state_env) for s in settings.services().values()}
    try:
        with tempfile.TemporaryDirectory(prefix="omni-multi-") as tmp:
            for name, svc in settings.services().items():
                os.environ[svc.state_env] = str(Path(tmp) / name)
                check(f"{name} 状态目录跟随 {svc.state_env}",
                      svc.state_dir() == str(Path(tmp) / name), svc.state_dir())
                check(f"{name} 登记表落在状态目录下",
                      Path(svc.registry_file()).parent == Path(tmp) / name,
                      svc.registry_file())
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ── 3. 后端目录解析 ────────────────────────────────────────────
def test_backend_dir_resolution() -> None:
    print("[3] 后端目录解析")
    saved = {k: os.environ.get(k) for k in
             ("OMNI_BACKEND_DIR", "OMNI_BUS_BACKEND_DIR", "OMNI_BACKEND_SERVER", "OMNI_BUS_BACKEND_SERVER")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        with tempfile.TemporaryDirectory(prefix="omni-backend-") as tmp:
            root = Path(tmp)
            for name, sub in (("link", "link"), ("bus", "bus")):
                service_dir = root / sub
                service_dir.mkdir(parents=True)
                (service_dir / "server.py").write_text("# stub\n", encoding="utf-8")

                # 3a. 环境变量指向"服务目录"
                os.environ["OMNI_BACKEND_DIR" if name == "link" else "OMNI_BUS_BACKEND_DIR"] = str(service_dir)
                check(f"{name}: 环境变量指向服务目录可识别",
                      Path(settings.backend_dir(name)) == service_dir, settings.backend_dir(name))

                # 3b. 环境变量指向"后端仓库根"
                os.environ["OMNI_BACKEND_DIR" if name == "link" else "OMNI_BUS_BACKEND_DIR"] = str(root)
                check(f"{name}: 环境变量指向仓库根可识别",
                      Path(settings.backend_dir(name)) == service_dir, settings.backend_dir(name))

                # 3c. OMNI_*_BACKEND_SERVER 直接给 server.py
                os.environ.pop("OMNI_BACKEND_DIR" if name == "link" else "OMNI_BUS_BACKEND_DIR", None)
                os.environ["OMNI_BACKEND_SERVER" if name == "link" else "OMNI_BUS_BACKEND_SERVER"] = \
                    str(service_dir / "server.py")
                check(f"{name}: *_BACKEND_SERVER 生效",
                      Path(settings.backend_server_py(name)) == service_dir / "server.py",
                      settings.backend_server_py(name))
                os.environ.pop("OMNI_BACKEND_SERVER" if name == "link" else "OMNI_BUS_BACKEND_SERVER", None)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ── 4. 命令组 → 服务映射 ───────────────────────────────────────
def test_service_for() -> None:
    print("[4] 命令组 → 服务映射")
    for group in ("serial", "net", "network", "modbus", "usb"):
        check(f"{group} → bus", runtime.service_for(_args(command=group)) == "bus",
              runtime.service_for(_args(command=group)))
    for group in ("probe", "flash", "rtt", "monitor", "debug", "commander", "system"):
        check(f"{group} → link", runtime.service_for(_args(command=group)) == "link",
              runtime.service_for(_args(command=group)))
    check("--service 覆盖命令组映射",
          runtime.service_for(_args(command="probe", service="bus")) == "bus")
    check("--service 覆盖到 link",
          runtime.service_for(_args(command="serial", service="link")) == "link")


# ── 5. 状态型命令拒绝 --managed ────────────────────────────────
def test_reject_managed() -> None:
    print("[5] 状态型命令拒绝 --managed")
    status_args = _args(command="probe", probe_sub="status", managed=True)
    problem = runtime.reject_managed(status_args)
    check("probe status --managed 被拒绝", bool(problem))
    check("拒绝提示点名服务", "omni backend start link" in (problem or ""), problem or "")

    bus_status = _args(command="serial", serial_sub="status", managed=True)
    check("bus 命令的拒绝提示点名 bus", "omni backend start bus" in (runtime.reject_managed(bus_status) or ""))

    check("非 status 命令允许 --managed",
          runtime.reject_managed(_args(command="probe", probe_sub="list", managed=True)) is None)
    check("未加 --managed 时不受影响",
          runtime.reject_managed(_args(command="probe", probe_sub="status", managed=False)) is None)


# ── 6. 已安装 GUI 探测 ────────────────────────────────────────
def test_autoinstall_candidates() -> None:
    print("[6] 已安装 GUI 探测")
    for name in ("link", "bus"):
        homes = autoinstall.candidate_homes(name)
        check(f"{name}: 有安装目录候选", bool(homes), str(homes))
        check(f"{name}: 候选去重", len(homes) == len({os.path.normcase(h) for h in homes}), str(homes))
        dirs = autoinstall.backend_dirs(name)
        check(f"{name}: 后端目录含 resources/python",
              any(d.endswith(os.path.join("resources", "python")) for d in dirs), str(dirs))

    link_homes = set(autoinstall.candidate_homes("link"))
    bus_homes = set(autoinstall.candidate_homes("bus"))
    check("link/bus 的安装目录候选不同", link_homes != bus_homes)

    # 环境变量显式指定时排在最前
    saved = os.environ.get("OMNI_LINK_HOME")
    try:
        os.environ["OMNI_LINK_HOME"] = r"C:\custom\omni-link"
        check("OMNI_LINK_HOME 优先", autoinstall.candidate_homes("link")[0] == r"C:\custom\omni-link",
              autoinstall.candidate_homes("link")[0])
    finally:
        if saved is None:
            os.environ.pop("OMNI_LINK_HOME", None)
        else:
            os.environ["OMNI_LINK_HOME"] = saved


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass
    try:
        test_services()
        test_state_env_override()
        test_backend_dir_resolution()
        test_service_for()
        test_reject_managed()
        test_autoinstall_candidates()
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("unexpected exception")
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
