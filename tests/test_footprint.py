#!/usr/bin/env python3
"""符号级体积报告的离线测试（构造一个最小 ELF32，不依赖工具链）。

用法::

    cd python
    python tests/test_footprint.py
"""

from __future__ import annotations

import os
import struct
import sys

PY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PY_DIR)

from omni_cli.builder.footprint import build_report, format_report, report_kinds

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        failures.append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def make_elf32(path: str) -> None:
    """一个 32 位小端 ELF：.text(8B, 一个 FUNC) + .data(4B, 一个 OBJECT)。"""

    shstr = b"\x00.text\x00.data\x00.symtab\x00.strtab\x00.shstrtab\x00"
    off_text = 52
    off_data = off_text + 8
    off_symtab = off_data + 4
    off_strtab = off_symtab + 3 * 16
    strtab = b"\x00main\x00counter\x00"
    off_shstrtab = off_strtab + len(strtab)

    ehdr = bytearray(52)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 1      # ELFCLASS32
    ehdr[5] = 1      # little endian
    ehdr[6] = 1      # version
    struct.pack_into("<HHIIIIIHHHHHH", ehdr, 16,
                     2, 40, 1, 0, 0, 0, 0, 52, 0, 0, 40, 6, 5)

    symtab = b""
    symtab += struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0)
    symtab += struct.pack("<IIIBBH", 1, 0x1000, 8, (1 << 4) | 2, 0, 1)   # main
    symtab += struct.pack("<IIIBBH", 6, 0x2000, 4, (1 << 4) | 1, 0, 2)   # counter

    shoff = off_shstrtab + len(shstr)

    def shdr(name, kind, flags, addr, offset, size, link=0, info=0, align=4, entsize=0):
        return struct.pack("<IIIIIIIIII", name, kind, flags, addr, offset,
                           size, link, info, align, entsize)

    sections = b"".join([
        shdr(0, 0, 0, 0, 0, 0),
        shdr(1, 1, 0x6, 0x1000, off_text, 8),          # .text  ALLOC|EXEC
        shdr(7, 1, 0x3, 0x2000, off_data, 4),          # .data  ALLOC|WRITE
        shdr(13, 2, 0, 0, off_symtab, len(symtab), link=4, info=1),   # .symtab
        shdr(21, 3, 0, 0, off_strtab, len(strtab)),    # .strtab
        shdr(29, 3, 0, 0, off_shstrtab, len(shstr)),   # .shstrtab
    ])
    struct.pack_into("<I", ehdr, 0x20, shoff)

    with open(path, "wb") as handle:
        handle.write(bytes(ehdr))
        handle.write(b"\x00" * 8)
        handle.write(b"\x00" * 4)
        handle.write(symtab)
        handle.write(strtab)
        handle.write(shstr)
        handle.write(sections)


def test_parser(tmp: str) -> None:
    print("== 1. ELF 符号解析 ==")
    path = os.path.join(tmp, "fake.axf")
    make_elf32(path)

    rom = build_report(path, "rom")
    check("ROM 总数取自段大小", rom["total"] == 8, str(rom["total"]))
    check("ROM 只含函数符号", [s["name"] for s in rom["symbols"]] == ["main"], str(rom["symbols"]))
    check("ROM 百分比正确", rom["symbols"][0]["percent"] == 100.0, str(rom["symbols"][0]))

    ram = build_report(path, "ram")
    check("RAM 总数取自段大小", ram["total"] == 4, str(ram["total"]))
    check("RAM 只含对象符号", [s["name"] for s in ram["symbols"]] == ["counter"], str(ram["symbols"]))

    lines = format_report(rom)
    check("表格含标题与大小", "ROM" in lines[0] and "8 B" in lines[0], lines[0])
    check("表格含符号行", any("main" in line for line in lines), str(lines))


def test_targets() -> None:
    print("== 2. -t 目标名解析 ==")
    check("rom-report", report_kinds("rom-report") == ["rom"])
    check("ram_report 别名", report_kinds("ram_report") == ["ram"])
    check("大小写不敏感", report_kinds("ROM-REPORT") == ["rom"])
    check("footprint 一次给两块", report_kinds("footprint") == ["rom", "ram"])
    check("逗号组合", report_kinds("rom-report,ram-report") == ["rom", "ram"])
    check("逗号去重", report_kinds("rom-report,rom_report") == ["rom"])
    check("普通目标不是报告", report_kinds("size") is None)
    check("拼错不透传成报告", report_kinds("rom-reportty") is None)
    check("空值不报错", report_kinds(None) is None)


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="omni-footprint-") as tmp:
        test_parser(tmp)
        test_targets()

    print()
    if failures:
        print(f"FAILED ({len(failures)}): " + ", ".join(failures))
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
