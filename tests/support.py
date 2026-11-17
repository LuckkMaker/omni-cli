"""测试支撑：仓根路径与 link 后端可用性。

CLI 有一部分"契约测试"直接断言后端内部实现（RTT 环形缓存、flash payload、
pack 管理、session recorder）。这些用例只有在能定位到后端源码时才有意义。
后端已迁到独立仓库 ``omni-backend``（服务在 ``link/``），可能的布局：

- 本仓库作为 omni-link 的 submodule：``<omni-link>/vendor/omni-cli/``
  → ``<omni-link>/vendor/omni-backend/link``
- 两个仓库平级：``<workspace>/omni-cli/`` → ``<workspace>/omni-backend/link``
- 旧布局（迁移前）：``<omni-link>/python``

解析顺序：环境变量 ``OMNI_LINK_PYTHON`` → submodule 布局 → 同级仓库 → 旧内嵌布局。
找不到后端时，相关用例打印 SKIP 而不是失败（CI 里不拉后端仓库）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_AVAILABLE: bool | None = None


def _candidate_dirs() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("OMNI_LINK_PYTHON")
    if env:
        candidates.append(Path(env).expanduser())
    # submodule 布局：<omni-link>/vendor/omni-cli/tests -> <omni-link>/vendor/omni-backend/link
    if len(REPO_ROOT.parents) >= 2:
        candidates.append(REPO_ROOT.parents[1] / "vendor" / "omni-backend" / "link")
        # 旧布局：后端曾在应用仓库的 python/ 下
        candidates.append(REPO_ROOT.parents[1] / "python")
    # 同级仓库布局（omni-cli 与 omni-backend 平级）
    candidates.append(REPO_ROOT.parent / "omni-backend" / "link")
    candidates.append(REPO_ROOT.parent / "omni-link" / "vendor" / "omni-backend" / "link")
    # 旧内嵌布局（防御性保留）
    candidates.append(REPO_ROOT.parent / "python")
    return candidates


def backend_available() -> bool:
    """找到 omni-link 后端源码则把其目录加入 sys.path 并返回 True。"""

    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    for candidate in _candidate_dirs():
        if (candidate / "core" / "rtt_backend.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            _AVAILABLE = True
            return True
    _AVAILABLE = False
    return False


def requires_backend(func):
    """后端不可用时把用例降级为 SKIP（脚本式测试没有 pytest.skip）。"""

    if backend_available():
        return func

    def _skip(*_args, **_kwargs):
        print(f"  SKIP  {func.__name__}（需要 omni-link 后端源码，设置 OMNI_LINK_PYTHON）")

    _skip.__name__ = func.__name__
    return _skip


#: 源码编码检查排除项：第三方内置源码、构建产物、虚拟环境。
_ENCODING_EXCLUDES = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}


def _iter_python_sources(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _ENCODING_EXCLUDES and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath) / name


def check_source_encoding(root: Path) -> list[str]:
    """检查第一方 .py：必须是合法 UTF-8 且不含 U+FFFD。返回问题列表。"""
    root = root.resolve()
    problems: list[str] = []
    for path in _iter_python_sources(root):
        rel = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as e:
            problems.append(f"{rel}: 无法读取: {e}")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            problems.append(f"{rel}: 不是合法 UTF-8（{e}）")
            continue
        if "\ufffd" in text:
            line = text[: text.index("\ufffd")].count("\n") + 1
            problems.append(f"{rel}:{line}: 含 U+FFFD 替换字符")
    return problems

