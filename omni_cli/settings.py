"""路径与后端端口/可执行文件解析。

本包已从 OMNI Link 应用仓库拆分为独立项目，因此不再假设
``<omni-link>/python/omni_cli`` 这种"包目录上两级就是应用仓库"的布局：

- 包目录：``<repo>/omni_cli``
- 仓库根：``<repo>``
- 后端位置：环境变量 / 持久化配置 / 仓库布局探测（含 submodule 布局），见
  ``_resolve_backend_server``。
- 可变状态一律写用户级目录，避免在 submodule 或仓库里留下运行时文件。
"""

import json
import os
import sys
from typing import Optional

# 包位于 <repo>/omni_cli/
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PACKAGE_DIR)

_FROZEN = getattr(sys, "frozen", False)


def _state_dir() -> str:
    """跨进程共享状态目录。

    必须与后端 server.py 的 ``_state_dir`` 计算方式保持一致：后端启动后会把
    ``{pid, port}`` 写入该目录下的 ``backend.json``，CLI 据此发现并复用同一个
    后端（应用托管的后端 / 手工启动的后端都可被找到）。
    """
    env = os.environ.get("OMNI_STATE_DIR")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "omni-link")


# 可变状态文件目录：始终用户级。写入源码目录会弄脏 git（尤其在 submodule 里），
# 而 onefile 打包产物的 __file__ 还是一次性解包目录，同样不能持久化。
_STATE_ROOT = _state_dir()


def _looks_like_backend(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "server.py"))


def _probe_repo_layout() -> Optional[str]:
    """从仓库根向上寻找 ``<ancestor>/python/server.py``。

    覆盖两种常见开发布局：

    - submodule：``<omni-link>/vendor/omni-cli`` → ``<omni-link>/python``
    - 独立克隆后与 omni-link 平级不构成父子关系，只能靠环境变量/持久化配置。
    """
    current = REPO_ROOT
    for _ in range(4):
        candidate = os.path.join(current, "python")
        if _looks_like_backend(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _resolve_backend_server() -> str:
    """定位后端 server.py，按优先级：

    1. 环境变量 ``OMNI_BACKEND_SERVER``（直接给 server.py 路径）
    2. 环境变量 ``OMNI_BACKEND_DIR``（给后端根目录）
    3. 持久化配置 ``cli.config.json`` 里的 ``backend_dir``（安装脚本写入）
    4. 仓库布局探测（submodule / 内嵌开发）
    5. 兜底：``<repo>/../../python/server.py`` 形态的旧布局提示路径
    """
    explicit = os.environ.get("OMNI_BACKEND_SERVER")
    if explicit:
        return os.path.abspath(explicit)
    backend_dir = os.environ.get("OMNI_BACKEND_DIR")
    if backend_dir:
        return os.path.join(os.path.abspath(backend_dir), "server.py")
    persisted = _read_cli_config().get("backend_dir")
    if persisted:
        return os.path.join(os.path.abspath(persisted), "server.py")
    probed = _probe_repo_layout()
    if probed:
        return os.path.join(probed, "server.py")
    # 找不到时保留一个可读的期望路径，用于错误提示（不要抛在 import 期）。
    return os.path.join(REPO_ROOT, "python", "server.py")


# 持久化配置：安装后可写入后端目录，免去每次设环境变量
CLI_CONFIG_FILE = os.path.join(_STATE_ROOT, "cli.config.json")


def _read_cli_config() -> dict:
    try:
        with open(CLI_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_backend_dir(path: str) -> str:
    """记录后端目录到持久化配置，返回写入的绝对路径。"""
    data = _read_cli_config()
    data["backend_dir"] = os.path.abspath(path)
    os.makedirs(os.path.dirname(CLI_CONFIG_FILE), exist_ok=True)
    with open(CLI_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data["backend_dir"]


BACKEND_SERVER_PY = _resolve_backend_server()
BACKEND_DIR = os.path.dirname(BACKEND_SERVER_PY)
# 运行时状态文件：记录常驻后端进程的 pid 与端口
RUNTIME_FILE = os.path.join(_STATE_ROOT, ".backend_runtime.json")
# 后端日志文件（stderr 落盘，失败时可排查）
LOG_FILE = os.path.join(_STATE_ROOT, ".backend.log")
# 常驻模式握手文件（server.py 的 stdout）——分离式进程无法保留管道，
# 重定向到文件后轮询解析端口 JSON。
HANDSHAKE_FILE = os.path.join(_STATE_ROOT, ".backend.handshake.jsonl")

# 后端写入的共享注册表（含 pid/port）
SHARED_RUNTIME_FILE = os.path.join(_state_dir(), "backend.json")


def read_shared_runtime() -> Optional[dict]:
    """读取后端写入的共享注册表；不存在或损坏时返回 None。"""
    try:
        with open(SHARED_RUNTIME_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def dev_config_file() -> Optional[str]:
    """开发态端口配置：后端目录上一级的 ``backend.config.json``。"""
    candidate = os.path.join(os.path.dirname(BACKEND_DIR), "backend.config.json")
    return candidate if os.path.isfile(candidate) else None


def read_config_port() -> Optional[int]:
    """读取 backend.config.json 中配置的后端端口"""
    path = dev_config_file()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        port = cfg.get("backendPort")
        return int(port) if port else None
    except Exception:
        return None


def read_runtime() -> Optional[dict]:
    """读取常驻后端运行时状态（pid + port + 启动方式）"""
    try:
        with open(RUNTIME_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_runtime(data: dict) -> None:
    os.makedirs(os.path.dirname(RUNTIME_FILE), exist_ok=True)
    with open(RUNTIME_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clear_runtime() -> None:
    if os.path.exists(RUNTIME_FILE):
        os.remove(RUNTIME_FILE)


def env_port() -> Optional[int]:
    """环境变量 OMNI_BACKEND_PORT 覆盖（与 Electron 主进程保持一致）"""
    try:
        v = os.environ.get("OMNI_BACKEND_PORT")
        if v and v.isdigit() and int(v) > 0:
            return int(v)
    except Exception:
        pass
    return None


def candidate_ports() -> list[int]:
    """按优先级收集候选后端端口（去重保留顺序）"""
    ports: list[int] = []
    # 共享注册表：当前真正在跑的后端（应用托管 / 手工启动）——优先复用，
    # 避免 CLI 另起一个后端去抢同一探针。
    shared = read_shared_runtime()
    if shared and shared.get("port"):
        ports.append(int(shared["port"]))
    rt = read_runtime()
    if rt and rt.get("port"):
        ports.append(int(rt["port"]))
    cfg = read_config_port()
    if cfg:
        ports.append(cfg)
    env = env_port()
    if env:
        ports.append(env)
    seen = set()
    out = []
    for p in ports:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out
