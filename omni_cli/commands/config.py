"""工程构建配置命令：config [generate|menuconfig|bsp|sync-keil]。

本地执行，不经过 OMNI Link 后端；实现位于 ``omni_cli.config``。
"""

from __future__ import annotations

import glob
import io
import json
import os
import re
import sys

from ..options import config_parser
from .. import context
from ..config.bspgen import run_bspgen
from ..context import ProjectError
from ..config.uvproj_sync import sync_uvprojx_project


def add_parser(sub):
    p = sub.add_parser(
        "config",
        parents=[config_parser()],
        help="工程构建配置（Kconfig/BSP/Keil 同步；本地执行）",
    )
    ps = p.add_subparsers(dest="config_sub", metavar="<sub>")

    s = ps.add_parser("generate", parents=[config_parser()],
                      help="生成/更新工程配置（Kconfig + BSP，可选 MSP/Keil 同步）")
    s.add_argument("--msp", action="store_true", default=False,
                   help="（已废弃，仅兼容保留）同时生成 driver_msp_config.h；"
                        "MSP 由 vendor 提供，该文件不被驱动引用")
    s.add_argument("--no-bsp", action="store_true", default=False,
                   help="跳过 bsp_config.h 生成")
    s.add_argument("--sync-keil", action="store_true", default=False,
                   help="生成后同步 Keil 工程（默认全部 *.uvprojx）")
    s.add_argument("--uvprojx", action="append", default=None, metavar="FILE",
                   help="只同步指定 Keil 工程（可重复；默认全部）")
    s.add_argument("--stop-on-change", action="store_true", default=False,
                   help="Keil 工程有变化时返回退出码 2")
    s.add_argument("--rules", choices=("auto", "table"), default="auto",
                   help="源文件规则来源：auto=解析 omni/**/CMakeLists.txt（默认），"
                        "table=退回手写规则表")
    s.set_defaults(func=_run_local(_generate))

    s = ps.add_parser("menuconfig", parents=[config_parser()],
                      help="打开交互式 menuconfig，保存后把改动写回 board.conf")
    s.add_argument("--no-save", action="store_true", default=False,
                   help="只更新头文件，不回写 board.conf（下次 configure 会丢失改动）")
    s.set_defaults(func=_run_local(_menuconfig))

    s = ps.add_parser("bsp", parents=[config_parser()],
                      help="从源码扫描生成 bsp_config.h")
    s.add_argument("--map", default=None, metavar="PATH",
                   help="（已废弃，仅兼容保留）链接 map 不再参与生成，该参数被忽略")
    s.set_defaults(func=_run_local(_bsp))

    s = ps.add_parser("sync-keil", parents=[config_parser()],
                      help="按已有 .config 同步 Keil 工程（默认全部 *.uvprojx）")
    s.add_argument("--uvprojx", action="append", default=None, metavar="FILE",
                   help="只同步指定 Keil 工程（可重复；默认全部）")
    s.add_argument("--stop-on-change", action="store_true", default=False,
                   help="Keil 工程有变化时返回退出码 2")
    s.add_argument("--rules", choices=("auto", "table"), default="auto",
                   help="源文件规则来源：auto=解析 omni/**/CMakeLists.txt（默认），"
                        "table=退回手写规则表")
    s.set_defaults(func=_run_local(_sync_keil))

    # `omni config`（不带子命令）= `omni config generate`
    p.set_defaults(func=_run_local(_generate), config_sub="generate")


def _opt(args, name, default=None):
    return getattr(args, name, default)


def _run_local(handler):
    def _run(args):
        try:
            return handler(args) or 0
        except ProjectError as exc:
            return _report_error(args, str(exc), exc.code)
        except KeyboardInterrupt:
            return _report_error(args, "interrupted", 1)
        except OSError as exc:
            return _report_error(args, f"file/process operation failed: {exc}", 1)
        except Exception as exc:  # 防止工程命令抛出未格式化 traceback
            return _report_error(args, f"{type(exc).__name__}: {exc}", 1)

    return _run


def _report_error(args, message: str, code: int) -> int:
    quiet = getattr(args, "quiet", False)
    if not quiet:
        if getattr(args, "json_", False):
            print(json.dumps(
                {"success": False, "exit_code": code, "error": message},
                ensure_ascii=False,
                indent=2,
            ))
        else:
            print(f"Error: {message}", file=sys.stderr)
    return code


def _json_mode(args) -> bool:
    return bool(getattr(args, "json_", False))


def _log_for(args):
    if _json_mode(args) or getattr(args, "quiet", False):
        return lambda message: None
    return print


def _err_for(args):
    if _json_mode(args) or getattr(args, "quiet", False):
        return io.StringIO()
    return sys.stderr


def _project_path(project_dir: str, value: str) -> str:
    """相对路径按工程目录解析（与旧脚本的 cwd=工程目录一致）。"""

    return value if os.path.isabs(value) else os.path.join(project_dir, value)


def _uvprojx_args(project_dir: str, values):
    return [_project_path(project_dir, value) for value in values] if values else None


def _finish(args, payload: dict, human: str = "") -> None:
    if getattr(args, "quiet", False):
        return
    if _json_mode(args):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if human:
        print(human)


def _generate(args) -> int:
    from ..config.generate import generate_project_config

    ctx = context.resolve_context(
        project_dir=args.project_dir,
        omni_root=args.omni_root,
        target=args.board,
    )
    err = _err_for(args)
    result = generate_project_config(
        project_dir=ctx.project_dir,
        omni_root=ctx.omni_root,
        target=ctx.target,
        msp=bool(_opt(args, "msp", False)),
        no_bsp=bool(_opt(args, "no_bsp", False)),
        sync_keil=bool(_opt(args, "sync_keil", False)),
        rules_mode=_opt(args, "rules", "auto"),
        uvprojx_paths=_uvprojx_args(ctx.project_dir, _opt(args, "uvprojx")),
        clean_config=True,
        config_dir=_opt(args, "config_dir"),
        log=_log_for(args),
        err=err,
    )

    exit_code = 0
    if _opt(args, "stop_on_change", False) and result.uvprojx_changed:
        exit_code = 2

    payload = {
        "success": exit_code == 0,
        "exit_code": exit_code,
        "project_dir": ctx.project_dir,
        "omni_root": ctx.omni_root,
        "board": ctx.target,
        "config_dir": result.config_dir,
        "generated": result.generated,
        "uvprojx_changed": result.uvprojx_changed,
        "uvprojx_files": result.uvprojx_files,
        "warnings": result.warnings,
        "log": result.log_lines,
        "stderr": err.getvalue() if hasattr(err, "getvalue") else "",
    }
    human = "Configuration generated."
    if exit_code == 2:
        human = "Keil project updated; reload/rebuild the project."
    _finish(args, payload, human)
    return exit_code


def _menuconfig(args) -> int:
    from ..config.generate import (
        generate_after_menuconfig,
        generate_project_config,
        read_config_symbols,
    )
    from ..config.menuconfig import run_menuconfig, save_menuconfig_changes

    if _json_mode(args) or getattr(args, "quiet", False):
        raise ProjectError("menuconfig 是交互式命令，不支持 --json / --quiet", code=2)
    if not sys.stdin.isatty():
        raise ProjectError(
            "menuconfig 需要交互式终端，请在 PowerShell/CMD 中运行，不要在 CI 管道中使用。",
            code=2,
        )
    ctx = context.resolve_context(
        project_dir=args.project_dir,
        omni_root=args.omni_root,
        target=args.board,
    )
    log = _log_for(args)
    config_dir = _opt(args, "config_dir")

    # 先按 prj.conf / board.conf 生成一次：它既是 menuconfig 的输入，也是后面
    # 判断"用户改了哪些项"的基线。
    baseline = generate_project_config(
        project_dir=ctx.project_dir,
        omni_root=ctx.omni_root,
        target=ctx.target,
        clean_config=True,
        config_dir=config_dir,
        log=log,
    )
    before = read_config_symbols(os.path.join(baseline.config_dir, ".config"))

    rc = run_menuconfig(
        project_dir=ctx.project_dir,
        omni_root=ctx.omni_root,
        target=ctx.target,
        config_dir=config_dir,
        log=log,
    )
    if rc != 0:
        return rc

    if _opt(args, "no_save", False):
        generate_after_menuconfig(
            project_dir=ctx.project_dir,
            omni_root=ctx.omni_root,
            target=ctx.target,
            config_dir=config_dir,
            log=log,
        )
        log("menuconfig: 未回写 board.conf（--no-save），下次 configure 会丢失改动")
        return 0

    board_conf, changed = save_menuconfig_changes(
        project_dir=ctx.project_dir,
        omni_root=ctx.omni_root,
        target=ctx.target,
        config_dir=baseline.config_dir,
        before=before,
        log=log,
    )
    # 用回写后的 board.conf 重新生成，保证头文件与"下次 configure"结果一致。
    generate_project_config(
        project_dir=ctx.project_dir,
        omni_root=ctx.omni_root,
        target=ctx.target,
        clean_config=True,
        config_dir=config_dir,
        log=log,
    )
    log(f"menuconfig saved: {len(changed)} 项写入 {board_conf}；头文件已更新。")
    return 0


def _bsp(args) -> int:
    from ..config.generate import resolve_config_dir

    ctx = context.resolve_context(
        project_dir=args.project_dir,
        target=args.board,
        require_omni=False,
    )
    map_path = _project_path(ctx.project_dir, args.map) if args.map else None
    if map_path:
        print(
            "Warning: --map 已废弃：bsp_config.h 现在只从源码扫描生成"
            "（链接 map 可能缺失、陈旧或属于别的板），该参数被忽略。",
            file=sys.stderr,
        )
    config_dir = resolve_config_dir(
        ctx.project_dir, ctx.target, _opt(args, "config_dir"))
    path = os.path.join(config_dir, "bsp_config.h")
    before = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            before = handle.read()

    log = _log_for(args)
    out = run_bspgen(ctx.project_dir, ctx.target, log=log,
                     config_dir=config_dir)
    after = None
    if os.path.exists(out):
        with open(out, "r", encoding="utf-8") as handle:
            after = handle.read()

    payload = {
        "success": True,
        "exit_code": 0,
        "project_dir": ctx.project_dir,
        "board": ctx.target,
        "config_dir": config_dir,
        "bsp_config": out,
        "changed": before != after,
    }
    _finish(args, payload, f"BSP header: {out}")
    return 0


def _sync_keil(args) -> int:
    from ..config.generate import resolve_config_dir

    ctx = context.resolve_context(
        project_dir=args.project_dir,
        omni_root=args.omni_root,
        require_target=False,
    )
    # require_target=False 时 ctx.target 恒为 None，这里单独解析一次，
    # 否则永远找不到 boards/<board>/config/.config。
    target = ctx.target
    if not target:
        try:
            target = context.resolve_target(ctx.project_dir, args.board)
        except ProjectError:
            target = None

    # .config 现在默认在生成物目录，按优先级收集候选：
    #   --config-dir → boards/<board>/config（cbuild / Keil）→ build/<board>/<compiler>/<type>/config
    #   （omni build）→ 工程根（旧布局）
    searched: list[str] = []
    explicit = _opt(args, "config_dir")
    dirs = []
    if explicit:
        dirs.append(resolve_config_dir(ctx.project_dir, target or "", explicit))
    if target:
        dirs.append(resolve_config_dir(ctx.project_dir, target))
        dirs.append(os.path.join(ctx.project_dir, "build", target, "cbuild"))
    dirs.append(ctx.project_dir)

    candidates = [os.path.join(directory, ".config") for directory in dirs]
    # 没指定 -b（或工程有多块板）时，按目录扫一遍；只有唯一命中才自动采用。
    candidates.extend(sorted(glob.glob(os.path.join(
        ctx.project_dir, "boards", "*", "config", ".config"))))
    candidates.extend(sorted(glob.glob(os.path.join(
        ctx.project_dir, "build", "*", "*", "*", "config", ".config"))))

    seen: set[str] = set()
    existing: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        searched.append(candidate)
        if os.path.exists(candidate):
            existing.append(candidate)

    dot_config = None
    if target and existing:
        dot_config = existing[0]
    elif len(existing) == 1:
        dot_config = existing[0]
    elif len(existing) > 1:
        raise ProjectError(
            "找到多个 .config，请用 -b/--board 或 --config-dir 指定：\n  "
            + "\n  ".join(existing),
            code=2,
        )
    if not dot_config:
        raise ProjectError(
            "未找到 .config；请先运行 omni config 生成配置。已查找：\n  "
            + "\n  ".join(searched),
            code=1,
        )
    if not target:
        # 让 uvprojx 同步知道是哪块板（从命中路径里反推 boards/<board>/）
        match = re.search(r"boards[\\/]+([^\\/]+)[\\/]+config", dot_config)
        if match:
            target = match.group(1)

    log = _log_for(args)
    changed, files = sync_uvprojx_project(
        ctx.project_dir,
        dot_config,
        uvprojx_paths=_uvprojx_args(ctx.project_dir, args.uvprojx),
        repo_root=ctx.repo_root,
        rules_mode=getattr(args, "rules", "auto"),
        board=target,
        log=log,
    )
    exit_code = 2 if (args.stop_on_change and changed) else 0
    payload = {
        "success": exit_code == 0,
        "exit_code": exit_code,
        "project_dir": ctx.project_dir,
        "changed": changed,
        "uvprojx": files,
    }
    human = "Keil project updated." if changed else "Keil project unchanged."
    _finish(args, payload, human)
    return exit_code
