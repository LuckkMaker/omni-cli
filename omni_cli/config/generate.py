"""工程配置生成编排（由 omni-emb 的 omni_config.py 移植）。

CLI 自己拥有这套逻辑，不再 subprocess 调用 omni/scripts/omni_config.py。
"""

from __future__ import annotations

import contextlib
import glob
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, TextIO

from ..context import ProjectError


@dataclass
class ConfigResult:
    project_dir: str
    omni_root: str
    target: str
    config_dir: str = ""
    generated: list[str] = field(default_factory=list)
    uvprojx_changed: bool = False
    uvprojx_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)


def resolve_project_configs(project_dir: str, target: str) -> list[str]:
    """按 omni_config.py 的优先级收集输入配置片段。"""

    configs: list[str] = []

    default_config = os.path.join(project_dir, "prj.conf")
    if os.path.exists(default_config):
        configs.append(default_config)

    target_candidates = [
        os.path.join(project_dir, "boards", target, "board.conf"),
        os.path.join(project_dir, "boards", target, "prj.conf"),
        os.path.join(project_dir, "boards", target, "config", "board.conf"),
        os.path.join(project_dir, "boards", target, "config", "prj.conf"),
        os.path.join(project_dir, f"{target}.conf"),
        os.path.join(project_dir, f"prj.{target}.conf"),
    ]
    for config_file in target_candidates:
        if os.path.exists(config_file):
            configs.append(config_file)
            break

    return configs


def resolve_project_kconfig(project_dir: str) -> Optional[str]:
    project_kconfig = os.path.join(project_dir, "Kconfig")
    return project_kconfig if os.path.exists(project_kconfig) else None


def resolve_config_dir(project_dir: str, target: str, config_dir: Optional[str] = None) -> str:
    """解析生成物目录。

    ``config_dir`` 为空时沿用 ``<project>/boards/<target>/config``（cbuild / Keil 依赖
    该路径，保持兼容）；相对路径按工程目录解析，与 ``--uvprojx`` 的口径一致。
    """

    if config_dir:
        if not os.path.isabs(config_dir):
            config_dir = os.path.join(project_dir, config_dir)
        return os.path.abspath(config_dir)
    return os.path.abspath(os.path.join(project_dir, "boards", target, "config"))


def _kconfig_abs(path: str) -> str:
    """Kconfig ``source`` 用绝对路径，避免依赖 cwd / $srctree。"""

    return os.path.abspath(path).replace("\\", "/")


# --------------------------------------------------------------------------- #
# menuconfig 回写：把交互式改动落到 board.conf 的受管块里
# --------------------------------------------------------------------------- #
MENUCONFIG_BEGIN = "# >>> omni menuconfig（本块由 omni config menuconfig 生成，勿手改）"
MENUCONFIG_END = "# <<< omni menuconfig"


def read_config_symbols(path: str) -> dict[str, str]:
    """读 ``.config`` / ``*.conf``：返回 ``{CONFIG_X: value}``，未启用记为 ``n``。"""

    values: dict[str, str] = {}
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return values
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                match = re.match(r"#\s*(CONFIG_[A-Za-z0-9_]+)\s+is not set$", line)
                if match:
                    values[match.group(1)] = "n"
                continue
            match = re.match(r"(CONFIG_[A-Za-z0-9_]+)=(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2)
    return values


def _strip_menuconfig_block(text: str) -> str:
    out: list[str] = []
    skipping = False
    for line in text.split("\n"):
        if line.startswith(MENUCONFIG_BEGIN):
            skipping = True
            continue
        if skipping:
            if line.startswith(MENUCONFIG_END):
                skipping = False
            continue
        out.append(line)
    return "\n".join(out)


def write_menuconfig_block(conf_path: str, values: dict[str, str]) -> bool:
    """把 ``values`` 写成 ``conf_path`` 末尾的受管块（先删旧块）；返回是否改动。

    ``values`` 为空表示清空旧块（用户把改动改回去了）。
    """

    raw = ""
    if os.path.exists(conf_path):
        with open(conf_path, "rb") as handle:
            raw = handle.read().decode("utf-8", errors="replace")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    stripped = _strip_menuconfig_block(text)

    if values:
        lines = [MENUCONFIG_BEGIN]
        for name in sorted(values):
            value = values[name]
            lines.append(f"# {name} is not set" if value == "n" else f"{name}={value}")
        lines.append(MENUCONFIG_END)
        separator = "\n\n" if stripped.strip() else ""
        new_text = stripped.rstrip("\n") + separator + "\n".join(lines) + "\n"
    else:
        new_text = stripped.rstrip("\n") + "\n" if stripped.strip() else ""

    if new_text == text:
        return False
    if crlf:
        new_text = new_text.replace("\n", "\r\n")
    directory = os.path.dirname(conf_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(conf_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)
    return True


def filter_user_settable(
    kconfig_file: str,
    values: dict[str, str],
    srctree: Optional[str] = None,
) -> dict[str, str]:
    """丢掉没有 prompt 的派生符号——它们不能出现在手写配置片段里。"""

    from kconfiglib import Kconfig

    saved = os.environ.get("srctree")
    if srctree:
        os.environ["srctree"] = srctree
    try:
        kconf = Kconfig(kconfig_file, warn_to_stderr=False, suppress_traceback=True)
    finally:
        if srctree:
            if saved is None:
                os.environ.pop("srctree", None)
            else:
                os.environ["srctree"] = saved
    kept: dict[str, str] = {}
    for name, value in values.items():
        # kconfiglib 的 syms 键是 Kconfig 里的符号名（无 CONFIG_ 前缀）
        symbol = kconf.syms.get(name[7:] if name.startswith("CONFIG_") else name)
        if symbol is None:
            continue
        if not any(node.prompt for node in symbol.nodes):
            continue
        kept[name] = value
    return kept


def create_combined_kconfig(
    omni_root: str,
    project_dir: str,
    tmp_dir: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """把应用 Kconfig 挂到 omni 主 Kconfig 树；返回 (kconfig_file, project_kconfig)。

    临时文件默认写在 ``omni_root``（历史行为），传入 ``tmp_dir`` 时改写在那里——
    omni 是只读子模块时仍可配置。
    """

    project_kconfig = resolve_project_kconfig(project_dir)
    default_kconfig = os.path.join(omni_root, "Kconfig")
    if not project_kconfig:
        return default_kconfig, None

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".Kconfig",
        prefix="omni-app-",
        dir=tmp_dir or omni_root,
        delete=False,
    )
    with temp_file as handle:
        handle.write('mainmenu "OMNI Configuration"\n\n')
        handle.write(f'source "{_kconfig_abs(os.path.join(omni_root, "Kconfig.omni"))}"\n')
        handle.write(f'source "{_kconfig_abs(project_kconfig)}"\n')
    return temp_file.name, project_kconfig


def _clean_old_config(dot_config: str) -> None:
    for path in (dot_config, dot_config + ".old"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            raise ProjectError(f"无法清理旧配置文件 {path}: {exc}") from exc


def _collect_env_restore() -> dict[str, Optional[str]]:
    return {
        "srctree": os.environ.get("srctree"),
        "OMNI_BASE": os.environ.get("OMNI_BASE"),
    }


def _restore_env(saved: dict[str, Optional[str]]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@contextlib.contextmanager
def _chdir(path: str):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def generate_project_config(
    *,
    project_dir: str,
    omni_root: str,
    target: str,
    msp: bool = False,
    no_bsp: bool = False,
    sync_keil: bool = False,
    rules_mode: str = "auto",
    uvprojx_paths: Optional[Iterable[str]] = None,
    clean_config: bool = True,
    config_dir: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    err: TextIO = None,
) -> ConfigResult:
    """生成工程配置。

    ``clean_config=False`` 用于 menuconfig 之后：保留用户刚保存的 ``.config``，
    只重新生成头文件。
    """

    import sys

    from .kconfig_merge import KconfigMergeError, merge_config

    log = log or (lambda message: print(message))
    err = err or sys.stderr
    result = ConfigResult(project_dir=project_dir, omni_root=omni_root, target=target)
    def emit(message: str) -> None:
        result.log_lines.append(message)
        log(message)

    configs = resolve_project_configs(project_dir, target)
    if not configs:
        raise ProjectError(
            "没有找到工程配置。需要 prj.conf，或 boards/"
            f"{target}/board.conf、boards/{target}/prj.conf、"
            f"{target}.conf、prj.{target}.conf 之一。",
            code=1,
        )

    config_dir = resolve_config_dir(project_dir, target, config_dir)
    os.makedirs(config_dir, exist_ok=True)
    result.config_dir = config_dir
    dot_config = os.path.join(config_dir, ".config")
    header_out = os.path.join(config_dir, "omni_config.h")
    log_file = os.path.join(config_dir, "kconfig_log.txt")
    # 本次解析到的 Kconfig 清单：构建系统据此挂 configure 依赖，
    # 改任意一个 Kconfig 都会触发重配（否则头文件静默沿用旧值）。
    sources_file = os.path.join(config_dir, "kconfig_sources.txt")

    if clean_config:
        _clean_old_config(dot_config)

    if not os.path.exists(dot_config):
        with open(dot_config, "w", encoding="utf-8") as handle:
            handle.write("# Initial config\n")

    kconfig_file, project_kconfig = create_combined_kconfig(
        omni_root, project_dir, tmp_dir=config_dir)
    default_kconfig = os.path.join(omni_root, "Kconfig")

    emit("----------------------------------------------------------------")
    emit("Running OMNI Kconfig Generator for Keil")
    emit(f"Project Dir: {project_dir}")
    emit(f"OMNI Root:   {omni_root}")
    emit(f"Target:      {target}")
    emit(f"Config Dir:  {config_dir}")
    emit(f"Project Kconfig: {project_kconfig or '<none>'}")
    emit("Input Conf:  " + ", ".join(configs))
    emit(f"Output Hdr:  {header_out}")
    emit("----------------------------------------------------------------")

    saved_env = _collect_env_restore()
    os.environ["srctree"] = omni_root
    try:
        with _chdir(omni_root):
            merge_config(
                kconfig_file=kconfig_file,
                config_out=dot_config,
                header_out=header_out,
                kconfig_list_out=log_file,
                configs_in=[dot_config, *configs] if clean_config else [dot_config],
                handwritten_input_configs=clean_config,
                omni_base=omni_root,
                srctree=omni_root,
                sources_out=sources_file,
                log=emit,
                err=err,
            )
    except KconfigMergeError as exc:
        raise ProjectError(str(exc), code=1) from exc
    finally:
        _restore_env(saved_env)
        if kconfig_file != default_kconfig and os.path.exists(kconfig_file):
            try:
                os.remove(kconfig_file)
            except OSError:
                pass

    result.generated.extend([dot_config, header_out, log_file, sources_file])

    if not no_bsp:
        try:
            from . import bspgen

            bsp_file = bspgen.run_bspgen(project_dir, target, log=emit,
                                        config_dir=config_dir)
            if bsp_file:
                result.generated.append(bsp_file)
        except Exception as exc:  # 保持 omni_config.py 的“警告但不中断”行为
            emit(f"Warning: omni_bspgen failed: {exc}")

    if msp:
        emit(
            "Warning: --msp 已废弃：生成的 driver_msp_config.h 不被任何驱动引用，"
            "引脚/时钟/DMA 由 vendor（CubeMX MSP、SoC 的 GPIO_Config）提供。"
            "该选项仅作兼容保留，将在后续版本移除。"
        )
        from . import mspconfig

        msp_file = mspconfig.run_msp_config(omni_root, project_dir, target, dot_config, log=emit)
        if msp_file:
            result.generated.append(msp_file)

    if sync_keil:
        from . import uvproj_sync

        changed, files = uvproj_sync.sync_uvprojx_project(
            project_dir,
            dot_config,
            uvprojx_paths=uvprojx_paths,
            repo_root=os.path.dirname(omni_root),
            rules_mode=rules_mode,
            board=target,
            log=emit,
        )
        result.uvprojx_changed = changed
        result.uvprojx_files = files

    return result


def generate_after_menuconfig(
    *,
    project_dir: str,
    omni_root: str,
    target: str,
    no_bsp: bool = False,
    config_dir: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    err: TextIO = None,
) -> ConfigResult:
    """menuconfig 保存后重新生成头文件，保留用户刚编辑的 .config。"""

    return generate_project_config(
        project_dir=project_dir,
        omni_root=omni_root,
        target=target,
        no_bsp=no_bsp,
        clean_config=False,
        config_dir=config_dir,
        log=log,
        err=err,
    )


def find_soc_template(omni_root: str, soc_family: str, soc_series: str) -> Optional[str]:
    """按 soc/{vendor}/{family}/{series}/driver_msp_config.h 查找模板。"""

    patterns = [
        os.path.join(omni_root, "soc", "*", soc_family, soc_series, "driver_msp_config.h"),
        os.path.join(omni_root, "soc", soc_family, soc_series, "driver_msp_config.h"),
    ]
    for pattern in patterns:
        found = glob.glob(pattern)
        if found:
            return found[0]
    return None
