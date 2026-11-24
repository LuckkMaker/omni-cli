"""启动交互式 menuconfig（由 omni_menuconfig.py 移植）。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from ..context import ProjectError
from .generate import create_combined_kconfig, resolve_config_dir

MENUCONFIG_STYLE = (
    "default path=fg:black,bg:white separator=fg:white,bg:blue,bold "
    "selection=fg:white,bg:red,bold help=path"
)
KCONFIG_CONFIG_HEADER = "#\n# Automatically generated file: Do not edit !!!\n#\n"


def _find_menuconfig() -> list[str]:
    executable = shutil.which("menuconfig")
    if executable:
        return [executable]

    # wheel 安装态：omni.exe 与 menuconfig.exe 通常在同一个 Scripts 目录。
    sibling = Path(sys.executable).with_name("menuconfig.exe" if os.name == "nt" else "menuconfig")
    if sibling.is_file():
        return [str(sibling)]

    if importlib.util.find_spec("menuconfig") is not None:
        return [sys.executable, "-m", "menuconfig"]
    raise ProjectError(
        "未找到 menuconfig 命令。请安装 kconfiglib（pip install kconfiglib），"
        "或把包含 menuconfig.exe 的 Scripts 目录加入 PATH。",
        code=1,
    )


def save_menuconfig_changes(
    *,
    project_dir: str,
    omni_root: str,
    target: str,
    config_dir: str,
    before: dict[str, str],
    log: Callable[[str], None] = print,
) -> tuple[str, dict[str, str]]:
    """把本次 menuconfig 的实际改动写回 ``boards/<target>/board.conf``。

    只写「相对 menuconfig 之前的有效配置发生变化」的符号，并且丢掉没有 prompt 的
    派生符号（否则下次 ``omni config generate`` 会因为"不可直接配置"而报错）。
    """

    from .generate import (
        create_combined_kconfig,
        filter_user_settable,
        read_config_symbols,
        write_menuconfig_block,
    )

    dot_config = os.path.join(config_dir, ".config")
    after = read_config_symbols(dot_config)

    changed: dict[str, str] = {}
    for name in set(before) | set(after):
        old = before.get(name)
        new = after.get(name, "n")
        if new != old:
            changed[name] = new

    if changed:
        kconfig_file, _ = create_combined_kconfig(omni_root, project_dir, tmp_dir=config_dir)
        try:
            changed = filter_user_settable(kconfig_file, changed, srctree=omni_root)
        finally:
            default_kconfig = os.path.join(omni_root, "Kconfig")
            if kconfig_file != default_kconfig and os.path.exists(kconfig_file):
                try:
                    os.remove(kconfig_file)
                except OSError:
                    pass

    board_conf = os.path.join(project_dir, "boards", target, "board.conf")
    if write_menuconfig_block(board_conf, changed):
        log(f"menuconfig: {len(changed)} 项改动写入 {board_conf}")
    else:
        log("menuconfig: 配置未变化，board.conf 保持不变")
    return board_conf, changed


def run_menuconfig(
    *,
    project_dir: str,
    omni_root: str,
    target: str,
    config_dir: Optional[str] = None,
    log: Callable[[str], None] = print,
) -> int:
    """打开 menuconfig；返回子进程退出码。"""

    config_dir = resolve_config_dir(project_dir, target, config_dir)
    os.makedirs(config_dir, exist_ok=True)
    dot_config = os.path.join(config_dir, ".config")

    kconfig_file, project_kconfig = create_combined_kconfig(
        omni_root, project_dir, tmp_dir=config_dir)
    default_kconfig = os.path.join(omni_root, "Kconfig")

    env = os.environ.copy()
    env["srctree"] = omni_root
    env["KCONFIG_CONFIG"] = dot_config
    env["KCONFIG_CONFIG_HEADER"] = KCONFIG_CONFIG_HEADER
    env["MENUCONFIG_STYLE"] = MENUCONFIG_STYLE

    log(f"OMNI Root:   {omni_root}")
    log(f"Target:      {target}")
    log(f"Config File: {dot_config}")

    try:
        cmd = [*_find_menuconfig(), kconfig_file]
        if os.name == "nt" and cmd[0].lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c", *cmd]
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            proc = subprocess.Popen(
                cmd,
                cwd=omni_root,
                env=env,
                creationflags=creationflags,
            )
            return proc.wait()
        return subprocess.call(cmd, cwd=omni_root, env=env)
    except FileNotFoundError as exc:
        raise ProjectError(f"启动 menuconfig 失败: {exc}", code=1) from exc
    finally:
        if kconfig_file != default_kconfig and os.path.exists(kconfig_file):
            try:
                os.remove(kconfig_file)
            except OSError:
                pass
