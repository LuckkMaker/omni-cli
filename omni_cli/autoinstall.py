"""已安装 GUI 的后端自动探测。

CLI 常常单独安装（wheel / portable zip），此时仓库布局探测必然失败；但只要用户装过
GUI，安装包里的后端就能直接复用。安装包布局（electron-builder `extraResources`）：

    <安装目录>/resources/python/omni-link-backend.exe # link 服务
    <安装目录>/resources/python/omni-bus-backend.exe  # bus 服务（阶段 3 之后）
    <安装目录>/resources/python/omni.exe              # CLI 自身

安装目录候选：

- 环境变量 ``OMNI_LINK_HOME`` / ``OMNI_BUS_HOME``（显式指定，优先级最高）
- 用户级安装（NSIS 默认）：``%LOCALAPPDATA%\\Programs\\omni-link``、``...\\omni-bus``
- 全局安装：``%PROGRAMFILES%\\OMNI Link``、``%PROGRAMFILES%\\OMNI Bus``
- 非 Windows：``/opt/omni-link``、``~/.local/share/omni-link``（同理 bus）

探测只读文件系统，不执行任何进程。
"""

import os
from typing import Optional

from . import settings


def _home_env_var(service_name: str) -> str:
    return "OMNI_LINK_HOME" if service_name == settings.SERVICE_LINK else f"OMNI_{service_name.upper()}_HOME"


def _install_dir_names(service_name: str) -> list[str]:
    """GUI 安装目录名候选（小写用于 Linux 路径，显示名用于 Program Files）。"""
    if service_name == settings.SERVICE_LINK:
        return ["omni-link", "OMNI Link", "OMNI-Link"]
    return ["omni-bus", "OMNI Bus", "OMNI-Bus"]


def candidate_homes(service_name: Optional[str] = None) -> list[str]:
    """候选 GUI 安装根目录（去重保留顺序）。"""
    svc = settings.service(service_name)
    homes: list[str] = []

    explicit = os.environ.get(_home_env_var(svc.name))
    if explicit:
        homes.append(os.path.abspath(explicit))

    if os.name == "nt":
        bases = [
            os.environ.get("LOCALAPPDATA") and os.path.join(os.environ["LOCALAPPDATA"], "Programs"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
        ]
    else:
        bases = [
            os.path.join(os.path.expanduser("~"), ".local", "share"),
            "/opt",
            "/usr/local",
        ]

    for base in bases:
        if not base:
            continue
        for name in _install_dir_names(svc.name):
            homes.append(os.path.join(base, name))

    seen = set()
    out: list[str] = []
    for home in homes:
        key = os.path.normcase(os.path.abspath(home))
        if key in seen:
            continue
        seen.add(key)
        out.append(os.path.abspath(home))
    return out


def backend_dirs(service_name: Optional[str] = None) -> list[str]:
    """候选后端目录（安装包里后端与 CLI 同在 ``resources/python``）。"""
    dirs: list[str] = []
    for home in candidate_homes(service_name):
        dirs.append(os.path.join(home, "resources", "python"))
        dirs.append(os.path.join(home, "python"))
    return dirs


def find_installed_backend(service_name: Optional[str] = None) -> Optional[str]:
    """返回已安装 GUI 的后端目录（含 server.py 或对应 exe），找不到返回 None。"""
    svc = settings.service(service_name)
    for path in backend_dirs(svc.name):
        if not os.path.isdir(path):
            continue
        if os.path.isfile(os.path.join(path, "server.py")):
            return path
        exe = os.path.join(path, svc.exe_name + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(exe):
            return path
    return None


def find_installed_exe(service_name: Optional[str] = None) -> Optional[str]:
    """返回已安装 GUI 的后端可执行文件路径，找不到返回 None。"""
    svc = settings.service(service_name)
    for path in backend_dirs(svc.name):
        exe = os.path.join(path, svc.exe_name + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(exe):
            return exe
    return None


def describe_search(service_name: Optional[str] = None) -> str:
    """给用户看的"我找过哪些地方"（用于报错提示）。"""
    svc = settings.service(service_name)
    lines = [f"  已检查的后端目录（{svc.name}）:"]
    for path in backend_dirs(svc.name):
        lines.append(f"    - {path}")
    lines.append(f"  可用环境变量：{_home_env_var(svc.name)} / {settings.service(svc.name).state_env}")
    return "\n".join(lines)


__all__ = [
    "candidate_homes",
    "backend_dirs",
    "find_installed_backend",
    "find_installed_exe",
    "describe_search",
]
