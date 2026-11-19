"""格式化与统一输出

默认输出可读表格；--json 时输出结构化 JSON，供脚本/CI 消费。
"""

import json
import os
import sys
import unicodedata

# ---------------------------------------------------------------- 颜色

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"

_VT_ENABLED: bool | None = None


def enable_vt(stream=None) -> bool:
    """Windows 控制台打开 ANSI/VT 处理；成功（或非 Windows）返回 True。

    Python 往 Windows 控制台写的是 WriteConsoleW，控制台默认不解释 ``\\033[``；
    需要显式置 ENABLE_VIRTUAL_TERMINAL_PROCESSING。拿不到句柄（重定向到文件/
    管道）时返回 False，调用方据此关闭颜色。
    """

    global _VT_ENABLED
    if os.name != "nt":
        return True
    if _VT_ENABLED is not None:
        return _VT_ENABLED

    _VT_ENABLED = False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]

        handle_id = -12 if stream is sys.stderr else -11  # STD_ERROR / STD_OUTPUT
        handle = kernel32.GetStdHandle(handle_id)
        mode = wintypes.DWORD()
        if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal_processing = 0x0004
        if kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing):
            _VT_ENABLED = True
    except Exception:  # 无控制台 / API 不可用：静默降级
        _VT_ENABLED = False
    return _VT_ENABLED


def color_enabled(mode: str = "auto", stream=None) -> bool:
    """按 ``--color`` / 环境变量 / 终端类型决定是否输出 ANSI。

    优先级：``always``/``never`` > ``NO_COLOR`` > ``OMNI_CLI_COLOR`` > auto。
    """

    stream = stream or sys.stdout
    mode = (mode or "auto").lower()
    if mode == "always":
        return True
    if mode == "never":
        return False

    if os.environ.get("NO_COLOR"):
        return False

    env = (os.environ.get("OMNI_CLI_COLOR") or "").strip().lower()
    if env in ("always", "1", "true", "yes"):
        return True
    if env in ("never", "0", "false", "no"):
        return False

    try:
        if not stream.isatty():
            return False
    except (AttributeError, OSError, ValueError):
        return False
    return enable_vt(stream)


class Palette:
    """ANSI 着色器；禁用时所有方法原样返回文本。"""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text, *codes) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + RESET

    def ok(self, text) -> str:
        return self(text, GREEN)

    def err(self, text) -> str:
        return self(text, RED)

    def warn(self, text) -> str:
        return self(text, YELLOW)

    def dim(self, text) -> str:
        return self(text, DIM)

    def cyan(self, text) -> str:
        return self(text, CYAN)

    def bold(self, text) -> str:
        return self(text, BOLD)


def configure_stdio() -> None:
    """把 CLI 的 stdout/stderr 固定为 UTF-8。

    背景：在管道/重定向下（PowerShell 7、CI、Node 子进程、`omni ... > log`），
    Python 按系统 locale 选择输出编码（中文 Windows 为 cp936/GBK），而消费端
    通常按 UTF-8 解码，中文就会变成 U+FFFD（实机测试报告 P1-4）。

    注意：
    - 只在未显式设置 ``PYTHONIOENCODING`` 时接管，尊重用户显式配置；
    - ``errors="backslashreplace"`` 而不是 ``strict``：任何字符都能输出，
      不会因 Windows 文件名代理字符等极端输入直接抛异常；同时也不会再产生 U+FFFD
      （U+FFFD 只会来自“按错误编码解码已有字节”，不会再由本进程引入）。
    - 交互式控制台本就由 Python 走 UTF-8（_WindowsConsoleIO），此处只影响重定向/管道。
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            # pythonw / 已关闭的流 / 非 TextIOWrapper：保持原样即可
            pass


class OutputWriter:
    """管理整体输出风格（json/quiet），并归一化退出码。"""

    def __init__(self, as_json: bool = False, quiet: bool = False):
        self.as_json = as_json
        self.quiet = quiet

    def emit(self, data, *, exit_code: int = 0) -> None:
        """输出结构化结果。data 为任意可 JSON 序列化对象。"""
        if self.quiet:
            sys.exit(exit_code)
        if self.as_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(data)
        sys.exit(exit_code)


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def display_width(text: str) -> int:
    """终端显示宽度：CJK 全角记 2 列，组合字符记 0 列。"""
    width = 0
    for ch in str(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    kept: list[str] = []
    used = 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + step > width - 1:
            break
        kept.append(ch)
        used += step
    return "".join(kept) + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def table_widths(headers: list[str], rows: list[list], cap: int = 60) -> list[int]:
    """按显示宽度计算每列宽度；多行单元格取最长一行。"""
    widths = [display_width(_to_str(h)) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            if index >= len(widths):
                continue
            widest = max(
                (display_width(line) for line in _to_str(cell).split("\n")), default=0
            )
            widths[index] = max(widths[index], widest)
    return [min(width, cap) for width in widths]


def render_table(
    headers: list[str],
    rows: list[list],
    *,
    widths: list[int] | None = None,
    show_header: bool = True,
) -> str:
    """无第三方依赖的表格渲染：按显示宽度对齐，单元格可用 ``\\n`` 换行。

    ``widths`` 可复用上一次算好的列宽（follow 模式用它保证跨批次对齐）。
    """
    rows_s = [[_to_str(c) for c in r] for r in rows]
    if widths is None:
        widths = table_widths(headers, rows_s)

    def render_row(cells: list[str]) -> list[str]:
        parts: list[list[str]] = []
        for index, cell in enumerate(cells):
            lines = cell.split("\n")
            if index < len(widths):
                lines = [_truncate(line, widths[index]) for line in lines]
            parts.append(lines)
        height = max((len(part) for part in parts), default=1)
        out: list[str] = []
        for line_index in range(height):
            cells_out = []
            for index, part in enumerate(parts):
                text = part[line_index] if line_index < len(part) else ""
                cells_out.append(_pad(text, widths[index]) if index < len(widths) else text)
            out.append("  ".join(cells_out).rstrip())
        return out

    lines: list[str] = []
    if show_header:
        lines.extend(render_row([_to_str(h) for h in headers]))
        lines.append("  ".join("-" * width for width in widths))
    for row in rows_s:
        lines.extend(render_row(row))
    return "\n".join(lines)


def print_table(headers: list[str], rows: list[list]) -> None:
    print(render_table(headers, rows))


def _to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def ctx_error(msg: str) -> int:
    print(f"错误: {msg}", file=sys.stderr)
    return 1