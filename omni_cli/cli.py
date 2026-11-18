"""CLI 命令分发。

- 两层子命令结构：`omni <group> <sub> [--全局选项]`
- 每个叶子命令绑定 run_command(handler) 包装：解析后端连接（托管/附加）、
  统一异常与退出码。
"""

import argparse
import shutil
import sys
import unicodedata

from . import __version__, client, commands, toolchain
from .commands import commander
from .formatting import configure_stdio, ctx_error, enable_vt

PROG = "omni"

# 顶层帮助「命令」区块的排版参数：分组名缩进 2，命令缩进 4，名称列对齐后接说明。
# 只做对齐与折行，不加颜色/符号装饰 —— 与 `python -h`、`pip -h` 的呈现保持一致。
_HEADING_INDENT = "  "
_COMMAND_INDENT = "    "
_MAX_HELP_WIDTH = 120

# ---------------------------------------------------------------------------
# 顶层命令分类（仅影响 `omni -h` 的分组显示，命令名与调用方式不变）。
# 分组标题只写功能域；「哪些组连 link / 哪些连 bus / 哪些本地执行」见 README
# 「后端连接的三种模式」与 `omni_cli/runtime.py`。
# ---------------------------------------------------------------------------
COMMAND_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Backend", ("backend",)),
    ("Debug & Flash",
     ("probe", "target", "flash", "debug", "commander", "monitor",
      "rtt", "device", "pack", "file", "system", "tool")),
    ("Project Build", ("boards", "build", "config", "modules", "toolchain")),
    ("Serial / Network / USB", ("serial", "net", "usb")),
]


class _GroupHeading:
    """分组标题占位：``help`` 为空，渲染时据此与真实命令区分。"""

    __slots__ = ("dest", "help")

    def __init__(self, title: str):
        self.dest, self.help = title, ""


def _grouped_commands(choices) -> list:
    """把子命令按 :data:`COMMAND_CATEGORIES` 分组（argparse 默认按注册序平铺）。

    返回「标题占位 + 组内命令」的扁平序列；未归类的命令落到 ``Other`` 兜底。
    """

    known = {c.dest for c in choices}
    out = []
    for title, names in COMMAND_CATEGORIES:
        present = [c for c in choices if c.dest in names]
        if not present:
            continue
        out.append(_GroupHeading(title))
        out.extend(present)
        for c in present:
            known.discard(c.dest)
    leftovers = [c for c in choices if c.dest in known]
    if leftovers:
        out.append(_GroupHeading("Other"))
        out.extend(leftovers)
    return out


def _char_width(ch: str) -> int:
    """单字符的终端显示宽度：全角/宽字符占 2 列。

    argparse 自带折行按 ``len()`` 算，中文帮助会被严重低估宽度，
    因此这里按显示宽度重算（中文 Windows 控制台/管道下表现一致）。
    """

    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _display_width(text: str) -> int:
    return sum(_char_width(ch) for ch in text)


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "-_./")


# 中文排版禁则：收尾标点不落行首，起始括号不留行尾。
_NO_LINE_START = "）】》」』，。、；：？！…"
_NO_LINE_END = "（【《「『"


def _wrap_display(text: str, width: int) -> list[str]:
    """按显示宽度折行。

    中文可在任意字符处断行；只有断点落在英文/标识符中间时才回退到词首，
    避免把 ``Commander`` / ``blank-check`` 这类词切成两半。
    """

    lines, cur = [], ""
    for ch in text:
        if not cur and ch == " ":  # 断行处的空格不带到行首
            continue
        if (cur and ch not in _NO_LINE_START
                and _display_width(cur) + _char_width(ch) > width):
            cut = len(cur)
            if _is_word_char(ch) and _is_word_char(cur[-1]):
                j = len(cur)
                while j and _is_word_char(cur[j - 1]):
                    j -= 1
                if j:  # 整个词都能挪到下一行时才回退
                    cut = j
            while cut and cur[cut - 1] in _NO_LINE_END:  # 开括号移到下一行
                cut -= 1
            cut = cut or len(cur)
            lines.append(cur[:cut].rstrip())
            cur = cur[cut:].lstrip()
        cur += ch
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _format_command(action, name_w: int, help_col: int, width: int) -> list[str]:
    """一条命令：名称列对齐，帮助文本按剩余宽度折行、续行对齐到帮助列。"""

    name = action.dest.ljust(name_w)
    wrapped = _wrap_display(action.help or "", max(width - help_col, 20))
    lines = [_COMMAND_INDENT + name + "  " + wrapped[0]]
    lines.extend(" " * help_col + line for line in wrapped[1:])
    return lines


class _CategorizedHelpParser(argparse.ArgumentParser):
    """顶层 ``omni -h``：子命令按功能域分组，分组名/缩进/列宽统一排版。

    只改帮助文本，命令解析流程仍完全交给 argparse。
    """

    def format_help(self) -> str:
        text = self._format_base()
        block = self._format_commands()
        if not block:
            return text
        marker = "\n" + self._optionals.title + ":\n"
        pos = text.rfind(marker)  # 命令块插在 options 段之前
        if pos < 0:
            return text + "\n" + block
        return text[:pos] + "\n" + block + "\n" + text[pos + 1:]

    def _format_base(self) -> str:
        """usage / description / options 仍由 argparse 渲染，保持与内置格式一致。"""

        fmt = self._get_formatter()
        fmt.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        fmt.add_text(self.description)
        if self.epilog:
            fmt.add_text(self.epilog)
        if self._optionals._group_actions:
            fmt.start_section(self._optionals.title)
            fmt.add_arguments(self._optionals._group_actions)
            fmt.end_section()
        return fmt.format_help()

    def _subcommands(self):
        return next((a for a in self._actions
                     if isinstance(a, argparse._SubParsersAction)
                     and getattr(a, "_choices_actions", None)), None)

    def _format_commands(self) -> str:
        sub = self._subcommands()
        if sub is None:
            return ""
        grouped = _grouped_commands(sub._choices_actions)
        name_w = max(len(c.dest) for c in sub._choices_actions)
        help_col = len(_COMMAND_INDENT) + name_w + 2
        width = max(min(shutil.get_terminal_size((100, 24)).columns,
                        _MAX_HELP_WIDTH), 48)
        lines = ["commands:"]
        for action in grouped:
            if not action.help:  # 分组标题
                if len(lines) > 1:  # 组间空行
                    lines.append("")
                lines.append(_HEADING_INDENT + action.dest)
                continue
            lines.extend(_format_command(action, name_w, help_col, width))
        return "\n".join(lines) + "\n"


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
    parser = _CategorizedHelpParser(
        prog=PROG,
        description="OMNI CLI - The OMNI meta-toolchain",
    )
    parser.add_argument("-v", "--version", action="version", version=f"omni {__version__}")
    # 子解析器保持 argparse 原生行为：分组只对顶层 ``omni -h`` 生效。
    sub = parser.add_subparsers(dest="command", metavar="<command>",
                                parser_class=argparse.ArgumentParser)
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
