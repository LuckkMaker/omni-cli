"""符号级体积报告（``omni build -t rom-report / ram-report``）。

直接从 ELF 的 symtab 读符号大小，不依赖链接器报告、也不需要 ``nm``/``fromelf``：
AC6 的 ``omni.axf`` 与 GCC/CLANG 的 ``omni.elf`` 都是 ELF32，同一套解析即可。

按段的属性分类：

* ROM：``SHF_ALLOC`` 且（可执行 或 非可写）——代码 + 只读数据；
* RAM：``SHF_ALLOC`` 且可写——``.data`` / ``.bss``。

总数取**段**的大小（含对齐与填充），符号明细用于回答「谁占的」，
所以明细之和一般略小于总数。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Optional

from ..context import ProjectError

REPORT_TARGETS = ("rom-report", "ram-report", "rom_report", "ram_report")

#: 支持的报告种类 -> (标题, ELF 段分类)
_REPORT_TITLES = {
    "rom": "ROM",
    "ram": "RAM",
}

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4

SHT_SYMTAB = 2
SHT_NOBITS = 8

STT_OBJECT = 1
STT_FUNC = 2
STT_SECTION = 3

_SHN_UNDEF = 0
_SHN_ABS = 0xFFF1
_SHN_COMMON = 0xFFF2


@dataclass
class Section:
    index: int
    name: str
    flags: int
    addr: int
    size: int
    type: int

    @property
    def in_rom(self) -> bool:
        return bool(self.flags & SHF_ALLOC) and (
            bool(self.flags & SHF_EXECINSTR) or not (self.flags & SHF_WRITE)
        )

    @property
    def in_ram(self) -> bool:
        return bool(self.flags & SHF_ALLOC) and bool(self.flags & SHF_WRITE)


@dataclass
class Symbol:
    name: str
    size: int
    section: str
    kind: str  # "rom" / "ram"
    value: int = 0
    anonymous: bool = False  # 段符号（如 STACK / HEAP），没有对应源码符号


@dataclass
class ElfSymbols:
    sections: list = field(default_factory=list)
    symbols: list = field(default_factory=list)

    def total(self, kind: str) -> int:
        if kind == "rom":
            return sum(section.size for section in self.sections if section.in_rom)
        return sum(section.size for section in self.sections if section.in_ram)


class _ElfReader:
    """最小 ELF32/ELF64 读取器：只取段表与符号表。"""

    def __init__(self, data: bytes) -> None:
        if len(data) < 52 or data[:4] != b"\x7fELF":
            raise ValueError("不是 ELF 文件")
        self.data = data
        self.is64 = data[4] == 2
        self.endian = "<" if data[5] == 1 else ">"

    def _u16(self, offset: int) -> int:
        return struct.unpack_from(self.endian + "H", self.data, offset)[0]

    def _u32(self, offset: int) -> int:
        return struct.unpack_from(self.endian + "I", self.data, offset)[0]

    def _u64(self, offset: int) -> int:
        return struct.unpack_from(self.endian + "Q", self.data, offset)[0]

    def sections(self) -> list:
        if self.is64:
            shoff = self._u64(0x28)
            shentsize = self._u16(0x3A)
            shnum = self._u16(0x3C)
            shstrndx = self._u16(0x3E)
            fmt = self.endian + "IIQQQQIIQQ"
        else:
            shoff = self._u32(0x20)
            shentsize = self._u16(0x2E)
            shnum = self._u16(0x30)
            shstrndx = self._u16(0x32)
            fmt = self.endian + "IIIIIIIIII"

        raw = []
        for index in range(shnum):
            offset = shoff + index * shentsize
            fields = struct.unpack_from(fmt, self.data, offset)
            raw.append(fields)

        strtab_off, strtab_size = raw[shstrndx][4], raw[shstrndx][5]
        names = self.data[strtab_off:strtab_off + strtab_size]

        def name_of(offset: int) -> str:
            if offset >= len(names):
                return ""
            end = names.find(b"\x00", offset)
            return names[offset:end].decode("utf-8", "replace") if end >= 0 else ""

        return [
            Section(index=i, name=name_of(fields[0]), flags=fields[2],
                    addr=fields[3], size=fields[5], type=fields[1])
            for i, fields in enumerate(raw)
        ]

    def symbols(self, sections: list) -> list:
        by_index = {section.index: section for section in sections}
        marks = {s.index for s in sections if s.type == SHT_NOBITS or s.size}

        for section in sections:
            if section.type != SHT_SYMTAB:
                continue
            if self.is64:
                shoff = self._u64(0x28)
                shentsize = self._u16(0x3A)
                fmt = self.endian + "IIQQQQIIQQ"
                sym_fmt = self.endian + "IBBHQQ"
                sym_size = 24
            else:
                shoff = self._u32(0x20)
                shentsize = self._u16(0x2E)
                fmt = self.endian + "IIIIIIIIII"
                sym_fmt = self.endian + "IIIBBH"
                sym_size = 16

            fields = struct.unpack_from(fmt, self.data, shoff + section.index * shentsize)
            sym_off = fields[4]
            sym_count = fields[5] // sym_size  # sh_size 是字节数
            strtab_index = fields[6]
            strtab_head = struct.unpack_from(
                fmt, self.data, shoff + strtab_index * shentsize)
            strtab = self.data[strtab_head[4]:strtab_head[4] + strtab_head[5]]

            out = []
            for i in range(sym_count):
                entry = struct.unpack_from(sym_fmt, self.data, sym_off + i * sym_size)
                if self.is64:
                    # Elf64_Sym: name, info, other, shndx, value, size
                    name_off, info, _other, shndx, value, size = entry
                else:
                    # Elf32_Sym: name, value, size, info, other, shndx
                    name_off, value, size, info, _other, shndx = entry
                if i == 0 or shndx in (_SHN_UNDEF, _SHN_ABS, _SHN_COMMON):
                    continue
                if size == 0 or shndx not in marks:
                    continue
                section = by_index.get(shndx)
                if section is None:
                    continue
                sym_type = info & 0xF
                if sym_type not in (STT_OBJECT, STT_FUNC, STT_SECTION):
                    continue
                end = strtab.find(b"\x00", name_off)
                name = strtab[name_off:end].decode("utf-8", "replace") if end >= 0 else ""
                if not name:
                    continue
                kind = "ram" if section.in_ram else ("rom" if section.in_rom else "")
                if not kind:
                    continue
                out.append(Symbol(name=name, size=size, section=section.name,
                                  kind=kind, value=value,
                                  anonymous=(sym_type == STT_SECTION)))
            return _drop_overlapping_sections(out)
        return []


def read_elf(path: str) -> ElfSymbols:
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ProjectError(f"读取 ELF 失败: {path}: {exc}", code=1) from exc
    try:
        reader = _ElfReader(data)
        sections = reader.sections()
        symbols = reader.symbols(sections)
    except (ValueError, struct.error) as exc:
        raise ProjectError(f"解析 ELF 失败: {path}: {exc}", code=1) from exc
    return ElfSymbols(sections=sections, symbols=symbols)


def build_report(path: str, kind: str, limit: int = 20) -> dict:
    """生成体积报告数据（供人类输出或 ``--json`` 消费）。"""

    if kind not in _REPORT_TITLES:
        raise ProjectError(f"未知体积报告类型: {kind}", code=2)

    elf = read_elf(path)
    total = elf.total(kind)
    picked = sorted((s for s in elf.symbols if s.kind == kind),
                    key=lambda s: (-s.size, s.name))
    top = picked[:limit]
    listed = sum(s.size for s in top)
    covered = sum(s.size for s in picked)
    return {
        "kind": kind,
        "elf": os.path.basename(path),
        "total": total,
        "symbols": [
            {
                "name": s.name,
                "size": s.size,
                "section": s.section,
                "percent": round(100.0 * s.size / total, 1) if total else 0.0,
            }
            for s in top
        ],
        "others": {
            "count": max(0, len(picked) - len(top)),
            "size": max(0, covered - listed),
        },
        "unattributed": max(0, total - covered),
    }


def format_report(report: dict) -> list:
    """把报告数据排成等宽表格行。"""

    title = _REPORT_TITLES.get(report["kind"], report["kind"])
    total = report["total"]
    lines = [f"{title} 占用（{report['elf']}）  总计 {total} B"]
    if not report["symbols"]:
        lines.append("  （没有符号级明细）")
        return lines
    width = max(len(item["name"]) for item in report["symbols"])
    width = min(max(width, 12), 48)
    for item in report["symbols"]:
        name = item["name"]
        if len(name) > width:
            name = name[: width - 3] + "..."
        lines.append(f"  {item['size']:>7d} B  {item['percent']:>5.1f}%  {name}")
    others = report["others"]
    if others["count"]:
        lines.append(
            f"  {others['size']:>7d} B  {'':>5}  其余 {others['count']} 个符号")
    if report.get("unattributed"):
        lines.append(
            f"  {report['unattributed']:>7d} B  {'':>5}  未归入任何符号（栈/堆/对齐）")
    return lines


#: ``-t`` 里可用的体积报告写法 -> 展开的报告种类
_REPORT_ALIASES = {
    "rom-report": ["rom"],
    "ram-report": ["ram"],
    "footprint": ["rom", "ram"],
    "report": ["rom", "ram"],
}


def report_kinds(target: Optional[str]) -> Optional[list]:
    """把 ``-t`` 的值解析成体积报告列表；不是报告目标时返回 ``None``。

    支持逗号组合（``rom-report,ram-report``）和一键两用（``footprint``）。
    只要有一个片段不认识，就整体当作 cmake 目标透传，避免静默吞掉拼写错误。
    """

    if not target:
        return None
    kinds: list = []
    for token in target.split(","):
        value = token.strip().replace("_", "-").lower()
        if not value:
            continue
        if value not in _REPORT_ALIASES:
            return None
        for kind in _REPORT_ALIASES[value]:
            if kind not in kinds:
                kinds.append(kind)
    return kinds or None


def _drop_overlapping_sections(symbols: list) -> list:
    """丢掉与真实符号范围重叠的段符号，避免重复计数。"""

    spans = {}
    for symbol in symbols:
        if symbol.anonymous:
            continue
        spans.setdefault(symbol.section, []).append((symbol.value, symbol.value + symbol.size))

    kept = []
    for symbol in symbols:
        if not symbol.anonymous:
            kept.append(symbol)
            continue
        start, end = symbol.value, symbol.value + symbol.size
        if any(not (end <= lo or start >= hi) for lo, hi in spans.get(symbol.section, [])):
            continue
        kept.append(symbol)
    return kept
