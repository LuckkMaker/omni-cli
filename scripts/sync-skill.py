#!/usr/bin/env python3
"""把 ``skills/<name>/`` 同步到外部技能目录。

技能源在 omni-cli 仓库内（``skills/omni-cli/``），运行时副本在
``omni-emb/.agents/skills/omni-cli/``。本脚本负责复制并检测漂移，避免两边手改。

用法::

    python scripts/sync-skill.py                 # 同步到默认目标
    python scripts/sync-skill.py --check         # 只检查，有漂移退出码 1
    python scripts/sync-skill.py --target PATH   # 指定目标目录

默认目标：``<omni-cli 同级>/omni-emb/.agents/skills/<name>``。
同步会删除目标中源不存在的文件（忽略 ``__pycache__`` / ``*.pyc``）。
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
IGNORE_DIRS = {"__pycache__"}
IGNORE_SUFFIXES = {".pyc", ".pyo"}


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix in IGNORE_SUFFIXES:
            continue
        files.append(path.relative_to(root))
    return sorted(files)


def default_target(name: str) -> Path:
    return REPO_ROOT.parent / "omni-emb" / ".agents" / "skills" / name


def diff(source: Path, target: Path) -> tuple[list[str], list[str], list[str]]:
    """返回 (缺失, 内容不同, 目标多余)。"""
    source_files = set(_iter_files(source))
    target_files = set(_iter_files(target)) if target.is_dir() else set()
    missing = sorted(str(item) for item in source_files - target_files)
    extra = sorted(str(item) for item in target_files - source_files)
    changed = sorted(
        str(item)
        for item in source_files & target_files
        if not filecmp.cmp(source / item, target / item, shallow=False)
    )
    return missing, changed, extra


def sync(source: Path, target: Path) -> tuple[int, int]:
    """复制源到目标并清理多余文件，返回 (写入数, 删除数)。"""
    target.mkdir(parents=True, exist_ok=True)
    written = 0
    for rel in _iter_files(source):
        dst = target / rel
        if dst.is_file() and filecmp.cmp(source / rel, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dst)
        written += 1

    removed = 0
    for rel in _iter_files(target):
        if not (source / rel).is_file():
            (target / rel).unlink()
            removed += 1
    # 清理空目录（不动目标根）
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_dir() and path != target and not any(path.iterdir()):
            path.rmdir()
    return written, removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步 skills/ 到外部技能目录")
    parser.add_argument("--name", default="omni-cli", help="技能名（默认 omni-cli）")
    parser.add_argument("--target", type=Path, help="目标目录（默认 ../omni-emb/.agents/skills/<name>）")
    parser.add_argument("--check", action="store_true", help="只检查漂移，不写文件")
    args = parser.parse_args(argv)

    source = SKILLS_DIR / args.name
    if not source.is_dir():
        print(f"错误: 技能源不存在: {source}", file=sys.stderr)
        return 2
    target = (args.target or default_target(args.name)).resolve()

    missing, changed, extra = diff(source, target)
    if args.check:
        if not (missing or changed or extra):
            print(f"SKILL 同步正常: {target}")
            return 0
        for label, items in (("缺失", missing), ("内容不同", changed), ("目标多余", extra)):
            for item in items:
                print(f"{label}: {item}")
        print(f"发现漂移，运行 `python scripts/sync-skill.py` 同步到 {target}")
        return 1

    written, removed = sync(source, target)
    print(f"已同步 {source} -> {target}（写入 {written}，删除 {removed}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
