"""已安装 GUI 的后端自动探测。

CLI 常常单独安装（wheel / portable zip），此时仓库布局探测必然失败；但只要用户装过
GUI，安装包里的后端就能直接复用。安装包布局（electron-builder `extraResources`）：

    <安装目录>/resources/python/omni-link-backend.exe # link 服务
    <安装目录>/resources/python/omni-bus-backend.exe  # bus 服务（阶段 3 之后）
    <安装目录>/resources/python/omni.exe              # CLI 自身

安装目录候选：

- 环境变量 ``OMNI_LINK_HOME`` / ``OMNI_BUS_HOME``（显式指定，优先级最高）
- 注册表卸载登记（``Uninstall`` 键的 ``InstallLocation`` / ``DisplayIcon`` /
  ``UninstallString``）：覆盖装到任意盘符、per-user 与 per-machine 两种安装
- 用户级安装（NSIS 默认）：``%LOCALAPPDATA%\\Programs\\omni-link``、``...\\omni-bus``
- 全局安装：``%PROGRAMFILES%\\OMNI Link``、``%PROGRAMFILES%\\OMNI Bus``
- 非 Windows：``/opt/omni-link``、``~/.local/share/omni-link``（同理 bus）

探测只读文件系统与注册表，不执行任何进程。
"""

import os
from typing import Optional

from . import settings

#: Windows 卸载登记表（per-user 与 per-machine 都在这一个键下）
_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"

#: 注册表探测结果缓存（进程内一次扫描，多次复用）
_registry_homes_cache: dict[str, tuple[str, ...]] = {}


def home_env_var(service_name: str) -> str:
    """显式指定安装根目录的环境变量名。"""
    return "OMNI_LINK_HOME" if service_name == settings.SERVICE_LINK else f"OMNI_{service_name.upper()}_HOME"


def _display_name_prefixes(service_name: str) -> tuple[str, ...]:
    """卸载条目 ``DisplayName`` 的匹配前缀（小写，忽略版本号后缀）。"""
    if service_name == settings.SERVICE_LINK:
        return ("omni link", "omni-link")
    return ("omni bus", "omni-bus")


def _home_has_backend(home: str, service_name: str) -> bool:
    """安装根目录下是否真的有该服务的后端（server.py 或打包 exe）。"""
    svc = settings.service(service_name)
    exe_name = svc.exe_name + (".exe" if os.name == "nt" else "")
    for sub in (os.path.join("resources", "python"), "python"):
        base = os.path.join(home, sub)
        if os.path.isfile(os.path.join(base, "server.py")):
            return True
        if os.path.isfile(os.path.join(base, exe_name)):
            return True
    return False


def _install_dir_from(field: str, raw: str) -> Optional[str]:
    """把卸载条目里的一个字段还原成安装目录。

    ``InstallLocation`` 可能为空（electron-builder 的 NSIS 常常不写），此时只能
    从 ``DisplayIcon``（``...\\OMNI Bus.exe,0``）或 ``UninstallString``
    （``"D:\\...\\Uninstall OMNI Bus.exe" /allusers``）反推。
    """
    text = raw.strip().strip('"')
    if not text:
        return None
    if field == "install_location":
        return text
    if field == "display_icon":
        path = text.rsplit(",", 1)[0].strip().strip('"')
        if not path:
            return None
        return os.path.dirname(path) if os.path.splitext(path)[1] else path
    # uninstall_string：去掉引号与命令行参数
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            text = text[1:end]
    else:
        for marker in (" /", "\t/", " -"):
            pos = text.find(marker)
            if pos > 0:
                text = text[:pos]
                break
    return text if os.path.isdir(text) else os.path.dirname(text)


def home_from_uninstall_entry(entry: dict, service_name: str) -> Optional[str]:
    """从一条卸载登记反推安装根目录。

    ``entry`` 至少要有 ``name``，否则按 ``install_location`` → ``display_icon`` →
    ``uninstall_string`` 的顺序尝试。名称不匹配、或目录里没有该服务的后端时返回 None。
    """
    name = (entry.get("name") or "").strip().lower()
    if not name or not name.startswith(_display_name_prefixes(service_name)):
        return None
    for field in ("install_location", "display_icon", "uninstall_string"):
        raw = entry.get(field)
        if not raw:
            continue
        home = _install_dir_from(field, str(raw))
        if home and _home_has_backend(home, service_name):
            return os.path.abspath(home)
    return None


def _reg_text(key, name: str) -> str:
    import winreg  # 仅在 Windows 上被调用

    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _uninstall_entries() -> list[dict]:
    """读取卸载登记表（HKLM 64/32 位视图 + HKCU），非 Windows 返回空。"""
    if os.name != "nt":
        return []
    import winreg

    views = [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, winreg.KEY_READ),
    ]
    entries: list[dict] = []
    for root, access in views:
        try:
            parent = winreg.OpenKey(root, _UNINSTALL_KEY, 0, access)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(parent, index)
                except OSError:
                    break
                index += 1
                try:
                    item = winreg.OpenKey(parent, sub, 0, winreg.KEY_READ)
                except OSError:
                    continue
                with item:
                    entries.append(
                        {
                            "name": _reg_text(item, "DisplayName"),
                            "install_location": _reg_text(item, "InstallLocation"),
                            "display_icon": _reg_text(item, "DisplayIcon"),
                            "uninstall_string": _reg_text(item, "UninstallString"),
                        }
                    )
        finally:
            parent.Close()
    return entries


def homes_from_registry(service_name: Optional[str] = None) -> list[str]:
    """从卸载登记表反查 GUI 安装根目录（任意盘符；非 Windows 返回空）。"""
    svc = settings.service(service_name)
    cached = _registry_homes_cache.get(svc.name)
    if cached is None:
        found: list[str] = []
        for entry in _uninstall_entries():
            home = home_from_uninstall_entry(entry, svc.name)
            if home and home not in found:
                found.append(home)
        cached = tuple(found)
        _registry_homes_cache[svc.name] = cached
    return list(cached)


def _install_dir_names(service_name: str) -> list[str]:
    """GUI 安装目录名候选（小写用于 Linux 路径，显示名用于 Program Files）。"""
    if service_name == settings.SERVICE_LINK:
        return ["omni-link", "OMNI Link", "OMNI-Link"]
    return ["omni-bus", "OMNI Bus", "OMNI-Bus"]


def candidate_homes(service_name: Optional[str] = None) -> list[str]:
    """候选 GUI 安装根目录（去重保留顺序）。"""
    svc = settings.service(service_name)
    homes: list[str] = []

    explicit = os.environ.get(home_env_var(svc.name))
    if explicit:
        homes.append(os.path.abspath(explicit))

    # 安装器登记的真实安装位置：不依赖盘符与安装模式
    homes.extend(homes_from_registry(svc.name))

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
    lines.append(f"  可用环境变量：{home_env_var(svc.name)}（安装根目录，其下含 resources\\python）")
    return "\n".join(lines)


__all__ = [
    "candidate_homes",
    "backend_dirs",
    "find_installed_backend",
    "find_installed_exe",
    "describe_search",
    "home_env_var",
    "home_from_uninstall_entry",
    "homes_from_registry",
]
