"""CLI 命令分发。

- 两层子命令结构：`omni <group> <sub> [--全局选项]`
- 每个叶子命令绑定 run_command(handler) 包装：解析后端连接（托管/附加）、
  统一异常与退出码。
"""

import argparse
import sys

from . import __version__, client, commands, toolchain
from .commands import commander
from .formatting import configure_stdio, ctx_error, enable_vt

PROG = "omni"

# ---------------------------------------------------------------------------
# 顶层命令分类（仅影响 `omni --help` 的分组显示，命令名与调用方式不变）。
# 分组同时标注了运行时路由：link / bus 命令组默认连接哪个后端服务。
# ---------------------------------------------------------------------------
COMMAND_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("后端进程管理（link / bus 生命周期）", ("backend",)),
    ("调试与烧录（连接 link 后端，--service 可覆盖）",
     ("probe", "target", "flash", "debug", "commander", "monitor",
      "rtt", "device", "pack", "file", "system", "tool")),
    ("工程构建（本地执行，无需后端）",
     ("boards", "build", "config", "modules", "toolchain")),
    ("串口与网络（连接 bus 后端，--service 可覆盖）", ("serial",)),
]


class _CategorizedHelpFormatter(argparse.HelpFormatter):
    """顶层 --help 按功能域分组渲染子命令（argparse 默认按注册序平铺）。

    实现方式：渲染子命令列表时临时把 ``_choices_actions`` 重排为
    「类别标题行 + 组内命令」的序列，标题行用 argparse 内部的
    ``_SubParsersAction._ChoicesPseudoAction`` 伪装成一条“命令”，
    从而复用原有的对齐与换行逻辑。
    """

    def _format_action(self, action):
        if (isinstance(action, argparse._SubParsersAction)
                and getattr(action, "_choices_actions", None)):
            original = action._choices_actions
            try:
                action._choices_actions = self._categorized(original)
                return super()._format_action(action)
            finally:
                action._choices_actions = original
        return super()._format_action(action)

    @staticmethod
    def _categorized(choices):
        known = {c.dest for c in choices}
        out = []
        for title, names in COMMAND_CATEGORIES:
            present = [c for c in choices if c.dest in names]
            if not present:
                continue
            out.append(argparse._SubParsersAction._ChoicesPseudoAction(title, [], ""))
            out.extend(present)
            for c in present:
                known.discard(c.dest)
        leftovers = [c for c in choices if c.dest in known]
        if leftovers:
            out.append(argparse._SubParsersAction._ChoicesPseudoAction("其他", [], ""))
            out.extend(leftovers)
        return out


def _format_api_error(e: client.ApiError) -> str:
    """按失败类型给出可执行的提示（区分未启动 / 僵死 / 业务错误）。"""
    kind = getattr(e, "kind", "http")
    if kind == "timeout":
        return (f"{e.detail}。后端进程可能已僵死，或正被挂起的硬件操作阻塞。\n"
                "  请执行 `omni backend stop` 停止后重新 `omni backend start`，再重试。")
    if kind == "unreachable":
        return (f"{e.detail}。\n"
                "  请先执行 `omni backend start`，或给命令加 --managed（临时拉起）"
                " / --backend-port <端口>（附加到已有后端）。")
    return str(e.detail)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="OMNI CLI - The OMNI meta-toolchain",
        formatter_class=_CategorizedHelpFormatter,
    )
    parser.add_argument("-v", "--version", action="version", version=f"omni {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    commands.add_all_parsers(sub)
    return parser


def main(argv=None) -> int:
    # 先挂上安装目录里的 tools/：cmake/ninja/clang/gcc 的子进程都继承这份 PATH。
    toolchain.prepare_environment()
    # 管道/重定向下必须显式 UTF-8，否则中文按系统 locale（中文 Windows=GBK）写出，
    # 调用方按 UTF-8 解码就成了 U+FFFD（实机报告 P1-4）。
    configure_stdio()
    # Windows 控制台默认不解释 ANSI；先打开 VT，彩色输出才生效（失败则静默）。
    enable_vt()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # commander exec 用 argparse.REMAINDER 收集命令参数，尾部全局选项会被吞成命令参数；
    # 解析前把已知全局选项前移，这样 `omni commander exec "read32 ..." --timeout 15` 生效。
    args = parser.parse_args(commander.normalize_argv(raw_argv))
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return getattr(args, "func")(args)
    except client.ApiError as e:
        return ctx_error(_format_api_error(e))
    except KeyboardInterrupt:
        return ctx_error("已中断")
