"""路径与后端端口/可执行文件解析（多服务：link / bus）。

本包已从 OMNI Link 应用仓库拆分为独立项目，因此不再假设
``<omni-link>/python/omni_cli`` 这种"包目录上两级就是应用仓库"的布局：

- 包目录：``<repo>/omni_cli``
- 仓库根：``<repo>``
- 后端位置：环境变量 / 持久化配置 / 仓库布局探测（含 submodule 布局）/ 已安装 GUI
  自动探测，见 ``backend_server_py``。
- 可变状态一律写用户级目录，避免在 submodule 或仓库里留下运行时文件。

两个后端服务各自独立（两个进程、两个登记表）：

===========  ================================  ==========================
服务         登记表                           状态目录环境变量
===========  ================================  ==========================
``link``     ``%LOCALAPPDATA%/omni-link``      ``OMNI_STATE_DIR``
``bus``      ``%LOCALAPPDATA%/omni-bus``       ``OMNI_BUS_STATE_DIR``
===========  ================================  ==========================
"""

import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

# 包位于 <repo>/omni_cli/
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PACKAGE_DIR)

_FROZEN = getattr(sys, "frozen", False)

SERVICE_LINK = "link"
SERVICE_BUS = "bus"
DEFAULT_SERVICE = SERVICE_LINK


def _platform_state_base() -> str:
    """用户级状态根目录（与后端 common/omni_service.py 保持一致）。"""
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )


@dataclass(frozen=True)
class ServiceInfo:
    """一个后端服务的身份与文件布局。

    字段必须与后端仓库 ``common/omni_service.py`` 的 ``SERVICE_ENV`` 一致，
    否则 CLI 找不到后端登记表。
    """

    #: 服务名（也是登记表里的 ``service`` 字段）
    name: str
    #: 状态目录环境变量
    state_env: str
    #: 状态目录默认名（``%LOCALAPPDATA%/<default_dir>``）
    default_dir: str
    #: 后端仓库内的服务子目录（``<omni-backend>/<backend_subdir>/``）
    backend_subdir: str
    #: PyInstaller 产物 exe 名（与后端 build.py 的 APP_NAME 一致）
    exe_name: str
    #: 开发态默认端口（仅用于提示；实际以后端 backend.config.json 为准）
    dev_port: int

    # ── 状态文件 ────────────────────────────────────────────────
    def state_dir(self) -> str:
        env = os.environ.get(self.state_env)
        if env:
            return env
        return os.path.join(_platform_state_base(), self.default_dir)

    def registry_file(self) -> str:
        """后端写入的共享登记表（含 pid/port/service/protocol）。"""
        return os.path.join(self.state_dir(), "backend.json")

    def runtime_file(self) -> str:
        """CLI 自己的常驻后端记录（由 ``backend start`` 写入）。"""
        return os.path.join(self.state_dir(), ".backend_runtime.json")

    def log_file(self) -> str:
        """后端 stderr 落盘位置（启动失败时排查用）。"""
        return os.path.join(self.state_dir(), ".backend.log")

    def handshake_file(self) -> str:
        """常驻模式握手文件（后端 stdout 的端口 JSON）。"""
        return os.path.join(self.state_dir(), ".backend.handshake.jsonl")


_SERVICES: dict[str, ServiceInfo] = {
    SERVICE_LINK: ServiceInfo(
        name=SERVICE_LINK,
        state_env="OMNI_STATE_DIR",
        default_dir="omni-link",
        backend_subdir="link",
        exe_name="omni-backend",
        dev_port=9001,
    ),
    SERVICE_BUS: ServiceInfo(
        name=SERVICE_BUS,
        state_env="OMNI_BUS_STATE_DIR",
        default_dir="omni-bus",
        backend_subdir="bus",
        exe_name="omni-bus-backend",
        dev_port=9004,
    ),
}


def services() -> dict[str, ServiceInfo]:
    """全部已知服务（名字 → 服务信息）。"""
    return dict(_SERVICES)


def service(name: Optional[str] = None) -> ServiceInfo:
    """按名字取服务信息；``None`` 取默认服务（link）。"""
    key = name or DEFAULT_SERVICE
    try:
        return _SERVICES[key]
    except KeyError:
        raise ValueError(
            f"未知服务 {key!r}；可选: {', '.join(sorted(_SERVICES))}"
        ) from None


def service_names() -> tuple[str, ...]:
    return tuple(_SERVICES)


# ---------------------------------------------------------------------------
# 后端源码位置
# ---------------------------------------------------------------------------
def _looks_like_backend(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "server.py"))


def _from_backend_root(root: str, svc: ServiceInfo) -> Optional[str]:
    """把"后端仓库根"或"服务目录"都归一成服务目录。

    既接受 ``<omni-backend>/link``（服务目录），也接受 ``<omni-backend>``
    （仓库根，会自动拼上服务子目录）；也兼容 GUI 安装包里的
    ``resources/python``（服务目录与 exe 同级）。
    """
    if not root:
        return None
    root = os.path.abspath(root)
    for candidate in (root, os.path.join(root, svc.backend_subdir)):
        if _looks_like_backend(candidate):
            return candidate
    return None


def _probe_repo_layout(svc: ServiceInfo) -> Optional[str]:
    """从仓库根向上寻找后端服务目录。

    覆盖这些布局：

    - submodule：``<omni-link>/vendor/omni-cli`` → ``<omni-link>/vendor/omni-backend/link``
    - 平级仓库：``<workspace>/omni-cli`` → ``<workspace>/omni-backend/link``
    - 旧内嵌布局：``<omni-link>/python``（迁移过渡期兜底，仅 link）
    """
    current = REPO_ROOT
    for _ in range(5):
        candidates = [
            os.path.join(current, "vendor", "omni-backend", svc.backend_subdir),
            os.path.join(current, "omni-backend", svc.backend_subdir),
            os.path.join(current, "omni-link", "vendor", "omni-backend", svc.backend_subdir),
        ]
        if svc.name == SERVICE_LINK:
            # 旧布局：后端就在应用仓库的 python/ 下
            candidates.append(os.path.join(current, "python"))
        for candidate in candidates:
            if _looks_like_backend(candidate):
                return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


# 持久化配置：安装后可写入后端目录，免去每次设环境变量。
# 位置沿用历史：link 的状态目录下（OMNI_STATE_DIR 覆盖时随之变化）。
CLI_CONFIG_FILE = os.path.join(_SERVICES[SERVICE_LINK].state_dir(), "cli.config.json")


def _read_cli_config() -> dict:
    try:
        with open(CLI_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _config_key(svc: ServiceInfo) -> str:
    """持久化配置里的键：link 沿用历史的 ``backend_dir``。"""
    return "backend_dir" if svc.name == SERVICE_LINK else f"{svc.name}_backend_dir"


def save_backend_dir(path: str, service_name: Optional[str] = None) -> str:
    """记录后端目录到持久化配置，返回写入的绝对路径。"""
    svc = service(service_name)
    data = _read_cli_config()
    data[_config_key(svc)] = os.path.abspath(path)
    os.makedirs(os.path.dirname(CLI_CONFIG_FILE), exist_ok=True)
    with open(CLI_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data[_config_key(svc)]


def _env_server_var(svc: ServiceInfo) -> str:
    return "OMNI_BACKEND_SERVER" if svc.name == SERVICE_LINK else f"OMNI_{svc.name.upper()}_BACKEND_SERVER"


def _env_dir_var(svc: ServiceInfo) -> str:
    return "OMNI_BACKEND_DIR" if svc.name == SERVICE_LINK else f"OMNI_{svc.name.upper()}_BACKEND_DIR"


def backend_dir(service_name: Optional[str] = None) -> str:
    """定位后端服务目录（含 server.py），按优先级：

    1. 环境变量 ``OMNI_BACKEND_SERVER`` / ``OMNI_BUS_BACKEND_SERVER``（直接给 server.py）
    2. 环境变量 ``OMNI_BACKEND_DIR`` / ``OMNI_BUS_BACKEND_DIR``（后端仓库根或服务目录）
    3. 持久化配置 ``cli.config.json``（安装脚本写入）
    4. 仓库布局探测（submodule / 平级仓库 / 旧内嵌）
    5. 已安装 GUI 的后端目录（``%LOCALAPPDATA%\\Programs\\omni-link\\resources\\python`` 等）

    找不到时返回一个可读的期望路径（用于错误提示，不在 import 期抛异常）。
    """
    svc = service(service_name)

    explicit = os.environ.get(_env_server_var(svc))
    if explicit:
        return os.path.dirname(os.path.abspath(explicit))

    env_dir = os.environ.get(_env_dir_var(svc))
    if env_dir:
        found = _from_backend_root(env_dir, svc)
        if found:
            return found
        return os.path.join(os.path.abspath(env_dir), svc.backend_subdir)

    persisted = _read_cli_config().get(_config_key(svc))
    if persisted:
        found = _from_backend_root(persisted, svc)
        if found:
            return found

    probed = _probe_repo_layout(svc)
    if probed:
        return probed

    # 已安装 GUI：装完即用，不需要设环境变量
    from . import autoinstall  # 局部导入：autoinstall 依赖本模块的 service()

    installed = autoinstall.find_installed_backend(svc.name)
    if installed:
        return installed

    return os.path.join(REPO_ROOT, svc.backend_subdir)


def backend_server_py(service_name: Optional[str] = None) -> str:
    """定位后端 ``server.py``（= ``backend_dir()/server.py``）。"""
    return os.path.join(backend_dir(service_name), "server.py")


# ---------------------------------------------------------------------------
# 兼容旧调用方的 link 常量（等价于 service("link") 的取值）
# ---------------------------------------------------------------------------
_LINK = _SERVICES[SERVICE_LINK]

BACKEND_SERVER_PY = backend_server_py(SERVICE_LINK)
BACKEND_DIR = os.path.dirname(BACKEND_SERVER_PY)
# 运行时状态文件：记录常驻后端进程的 pid 与端口
RUNTIME_FILE = _LINK.runtime_file()
# 后端日志文件（stderr 落盘，失败时可排查）
LOG_FILE = _LINK.log_file()
# 常驻模式握手文件（server.py 的 stdout）——分离式进程无法保留管道，
# 重定向到文件后轮询解析端口 JSON。
HANDSHAKE_FILE = _LINK.handshake_file()
# 后端写入的共享注册表（含 pid/port）
SHARED_RUNTIME_FILE = _LINK.registry_file()


# ---------------------------------------------------------------------------
# 登记表 / 运行时记录
# ---------------------------------------------------------------------------
def read_shared_runtime(service_name: Optional[str] = None) -> Optional[dict]:
    """读取后端写入的共享登记表；不存在或损坏时返回 None。"""
    try:
        with open(service(service_name).registry_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_runtime(service_name: Optional[str] = None) -> Optional[dict]:
    """读取常驻后端运行时状态（pid + port + 启动方式）"""
    try:
        with open(service(service_name).runtime_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_runtime(data: dict, service_name: Optional[str] = None) -> None:
    path = service(service_name).runtime_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clear_runtime(service_name: Optional[str] = None) -> None:
    path = service(service_name).runtime_file()
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# 端口
# ---------------------------------------------------------------------------
def dev_config_file(service_name: Optional[str] = None) -> Optional[str]:
    """开发态端口配置：优先服务目录自己的 ``backend.config.json``，
    其次后端仓库根的（旧布局/兼容）。"""
    backend = backend_dir(service_name)
    for candidate in (
        os.path.join(backend, "backend.config.json"),
        os.path.join(os.path.dirname(backend), "backend.config.json"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def read_config_port(service_name: Optional[str] = None) -> Optional[int]:
    """读取 backend.config.json 中配置的后端端口"""
    path = dev_config_file(service_name)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        port = cfg.get("backendPort")
        return int(port) if port else None
    except Exception:
        return None


def env_port(service_name: Optional[str] = None) -> Optional[int]:
    """环境变量端口覆盖：link 用 ``OMNI_BACKEND_PORT``，bus 用 ``OMNI_BUS_BACKEND_PORT``。

    与 Electron 主进程 / GUI 侧保持一致（link 沿用历史变量名）。
    """
    svc = service(service_name)
    var = "OMNI_BACKEND_PORT" if svc.name == SERVICE_LINK else f"OMNI_{svc.name.upper()}_BACKEND_PORT"
    try:
        v = os.environ.get(var)
        if v and v.isdigit() and int(v) > 0:
            return int(v)
    except Exception:
        pass
    return None


def candidate_ports(service_name: Optional[str] = None) -> list[int]:
    """按优先级收集候选后端端口（去重保留顺序）"""
    ports: list[int] = []
    # 共享登记表：当前真正在跑的后端（应用托管 / 手工启动）——优先复用，
    # 避免 CLI 另起一个后端去抢同一设备。
    shared = read_shared_runtime(service_name)
    if shared and shared.get("port"):
        ports.append(int(shared["port"]))
    rt = read_runtime(service_name)
    if rt and rt.get("port"):
        ports.append(int(rt["port"]))
    cfg = read_config_port(service_name)
    if cfg:
        ports.append(cfg)
    env = env_port(service_name)
    if env:
        ports.append(env)
    seen = set()
    out = []
    for p in ports:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out
