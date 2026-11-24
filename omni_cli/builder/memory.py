"""内存区域占用：从链接产物里读出 FLASH/RAM 等区域的「已用 / 上限」，并画 ASCII 进度条。

数据来源（按编译器）：

- GCC / CLANG：链接时 ``-Wl,--print-memory-usage`` 的输出（已用字节数精确），
  区域上限取链接 map 的 ``Memory Configuration`` 段（``64 KB`` / ``1 MB`` 这类
  人类可读单位换算回来会丢精度）。增量构建没有重新链接时，按 map 里输出段的
  地址与 LMA 求和；lld 的 map 根本不含区域上限（它只在链接时打印），这时复用
  上一次链接缓存到 ``<build-dir>/omni.memory.json`` 的结果。
- AC6 / AC5：armlink map 里的 ``Load Region`` / ``Execution Region`` 行本身就是
  「名称 + 已用 + 上限」，直接解析，不依赖额外开关。

区域名沿用链接脚本里的名字（``FLASH`` / ``RAM`` / ``CCMRAM``）；AC6 的
``LR_ROM`` / ``RW_RAM`` 会去掉 ``LR_`` / ``RW_`` 之类前缀，单个加载区命名为
``FLASH``，与 GCC 侧保持同一套读法。
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass
from typing import Iterable, Optional

from ..formatting import GREEN, RED, YELLOW, Palette

#: 进度条宽度（字符数）；宽度固定，区域多少都不换行。
BAR_WIDTH = 20
#: 占用率分档：低于 LOW 绿色、低于 HIGH 黄色、其余红色。
LOW_USAGE = 70.0
HIGH_USAGE = 90.0

_UNITS = {"B": 1, "KB": 1024, "MB": 1024 * 1024, "GB": 1024 * 1024 * 1024}

#: ``ld --print-memory-usage`` 的表头。
USAGE_HEADER = re.compile(r"^Memory region\s+Used Size\s+Region Size\s+%age Used$")
#: ``ld`` / ``lld`` ``--print-memory-usage`` 的数据行（lld 的 0 会写成 ``0 GB``）。
USAGE_ROW = re.compile(
    r"^\s*(?P<name>\S+):\s+(?P<used>\d+)\s+(?P<used_unit>B|KB|MB|GB)\s+"
    r"(?P<cap>\d+)\s+(?P<cap_unit>B|KB|MB|GB)\s+(?P<pct>[\d.]+)%"
)
#: GNU ld map 的 ``Memory Configuration`` 行：名字 / 起始 / 长度。
GNU_CONFIG_ROW = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s+0x(?P<origin>[0-9a-fA-F]+)\s+"
    r"0x(?P<length>[0-9a-fA-F]+)(?:\s+\S+)?$"
)
#: GNU ld map 的输出段行；带 ``load address`` 的是 .data 这类初值在 ROM 的段。
GNU_SECTION_ROW = re.compile(
    r"^(?P<name>\.[^\s]+)\s+0x(?P<addr>[0-9a-fA-F]+)\s+0x(?P<size>[0-9a-fA-F]+)"
    r"(?:\s+load address\s+0x(?P<lma>[0-9a-fA-F]+))?\s*$"
)
#: 段名单独一行、地址/大小在下一行（``._user_heap_stack`` 这种长名字）。
GNU_SECTION_TAIL = re.compile(
    r"^\s+0x(?P<addr>[0-9a-fA-F]+)\s+0x(?P<size>[0-9a-fA-F]+)"
    r"(?:\s+load address\s+0x(?P<lma>[0-9a-fA-F]+))?\s*$"
)
#: armlink map 的加载区/执行区行。
ARM_REGION_ROW = re.compile(
    r"^\s*(?P<kind>Load|Execution) Region (?P<name>\S+) "
    r"\(Base: 0x(?P<base>[0-9a-fA-F]+), Size: 0x(?P<size>[0-9a-fA-F]+), "
    r"Max: 0x(?P<max>[0-9a-fA-F]+)"
)
#: 视为「未设上限」的 Max。
_UNLIMITED = 0xFFFFFFFF
_ARM_PREFIXES = ("LR_", "ER_", "RO_", "RW_", "ZI_")
#: 增量构建（ninja: no work to do）时没有链接输出，用上一次链接的结果。
CACHE_NAME = "omni.memory.json"


@dataclass(frozen=True)
class MemoryRegion:
    """一个链接内存区域：名字、已用字节数、上限字节数。"""

    name: str
    used: int
    capacity: int

    @property
    def percent(self) -> float:
        if self.capacity <= 0:
            return 0.0
        return self.used / self.capacity * 100.0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "used": self.used,
            "capacity": self.capacity,
            "percent": round(self.percent, 2),
        }


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


# ---------------------------------------------------------------- 解析：GCC / CLANG


def parse_gnu_usage(text: str) -> list:
    """解析链接器 ``--print-memory-usage`` 的输出块（已用字节数取自链接器本身）。

    GNU ld 每行前后各插一个空行，llvm lld 不插；两者都能读。
    """

    regions = []
    inside = False
    for line in (text or "").splitlines():
        if USAGE_HEADER.match(line.strip()):
            inside = True
            continue
        if not inside:
            continue
        match = USAGE_ROW.match(line)
        if match:
            regions.append(MemoryRegion(
                match.group("name"),
                int(match.group("used")) * _UNITS[match.group("used_unit")],
                int(match.group("cap")) * _UNITS[match.group("cap_unit")],
            ))
            continue
        if line.strip():
            break  # 块结束（后面不再有区域行）
    return regions


def parse_gnu_configuration(text: str) -> list:
    """解析 GNU ld map 的 ``Memory Configuration`` 段：(名字, 起始, 容量)。

    ``*default*`` 之类不参与占用统计（名字不匹配、长度是 0xFFFFFFFF）。
    """

    rows = []
    inside = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped == "Memory Configuration":
            inside = True
            continue
        if inside and stripped.startswith("Linker script and memory map"):
            break
        if not inside or not stripped:
            continue
        match = GNU_CONFIG_ROW.match(stripped)
        if not match:
            continue
        capacity = int(match.group("length"), 16)
        if capacity <= 0 or capacity >= _UNLIMITED:
            continue
        rows.append((match.group("name"), int(match.group("origin"), 16), capacity))
    return rows


def parse_gnu_sections(text: str) -> list:
    """解析 GNU ld map 的输出段：(地址, 大小, LMA 或 None)。

    段名很长时 ld 会把名字单独放一行、地址和大小放下一行，这里一并处理
    （``._user_heap_stack`` 就属于这种，漏掉会显著低估 RAM 占用）。
    """

    rows = []
    pending = None
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        name_only = line.startswith(".") and " " not in line.strip()
        if name_only:
            pending = line.strip()
            continue
        match = GNU_SECTION_ROW.match(line.rstrip())
        if not match and pending is not None:
            match = GNU_SECTION_TAIL.match(line.rstrip())
        pending = None
        if not match:
            continue
        size = int(match.group("size"), 16)
        if size <= 0:
            continue
        lma = int(match.group("lma"), 16) if match.group("lma") else None
        rows.append((int(match.group("addr"), 16), size, lma))
    return rows


def gnu_regions(map_text: str, output_text: str = "", *, elf_path: str = "") -> list:
    """GCC/CLANG 的内存区域：优先用链接器自报的占用，其次按 map 的段地址估算。"""

    config = parse_gnu_configuration(map_text)
    usage = parse_gnu_usage(output_text)
    if usage:
        exact = {name: capacity for name, _, capacity in config}
        return [
            MemoryRegion(region.name, region.used, exact.get(region.name, region.capacity))
            for region in usage
        ]

    return _gnu_regions_from_sections(map_text, config, elf_path=elf_path)


def _gnu_regions_from_sections(map_text: str, config: list,
                               nobits_addrs: Iterable = (), elf_path: str = "") -> list:
    """按输出段的 VMA / LMA 归属到 ``Memory Configuration`` 里的区域。

    ``.bss`` / ``._user_heap_stack`` 这类 NOBITS 段只占 RAM、不在镜像里，
    但它们也可能带一个指进 FLASH 的 LMA（链接脚本里 ``>RAM AT>FLASH`` 的残留），
    所以先查 ELF 的段类型，避免把 ZI 算成 Flash 占用。
    """

    skip_lma = set(nobits_addrs) or _read_nobits(elf_path)
    used = {name: 0 for name, _, _ in config}

    def attribute(address: int, size: int) -> None:
        if address == 0:
            return
        for name, origin, capacity in config:
            if capacity > 0 and origin <= address < origin + capacity:
                used[name] += size
                return

    for address, size, lma in parse_gnu_sections(map_text):
        attribute(address, size)          # 占用运行位置（RAM）
        if lma is not None and lma != address and address not in skip_lma:
            attribute(lma, size)          # 初值占用加载位置（FLASH）

    return [MemoryRegion(name, used[name], capacity) for name, _, capacity in config]


def _read_nobits(elf_path: str) -> set:
    """ELF 里 ``SHT_NOBITS`` 段的地址集合（.bss 这类不进镜像的段）。"""

    if not elf_path:
        return set()
    try:
        with open(elf_path, "rb") as handle:
            data = handle.read()
    except OSError:
        return set()
    if data[:4] != b"\x7fELF" or len(data) < 52:
        return set()
    try:
        header = struct.unpack_from("<HHIQQQIHHHHHH" if data[4] == 2 else
                                    "<HHIIIIIHHHHHH", data, 16)
    except struct.error:
        return set()
    shoff, shentsize, shnum = header[5], header[10], header[11]
    if not shoff or not shnum or not shentsize:
        return set()
    section = ("<IIQQQQIIQQ" if data[4] == 2 else "<IIIIIIIIII")
    addresses = set()
    for index in range(shnum):
        try:
            fields = struct.unpack_from(section, data, shoff + index * shentsize)
        except struct.error:
            break
        if fields[1] == 8:  # SHT_NOBITS
            addresses.add(fields[3])
    return addresses


# ---------------------------------------------------------------- 解析：AC6 / AC5


def _arm_label(name: str, single_load: bool, kind: str) -> str:
    if kind == "Load" and single_load:
        return "FLASH"
    label = name
    for prefix in _ARM_PREFIXES:
        if label.startswith(prefix) and len(label) > len(prefix):
            label = label[len(prefix):]
            break
    return label or name


def parse_arm_regions(text: str) -> list:
    """解析 armlink map：加载区算 FLASH，执行区里不在加载区范围内的算 RAM/CCM。

    同一个落点、同一个上限的执行区会合并（``RW_NOINIT`` + ``RW_RAM`` 都是 RAM）。
    """

    loads = []
    execs = []
    for line in (text or "").splitlines():
        match = ARM_REGION_ROW.match(line)
        if not match:
            continue
        item = (
            int(match.group("base"), 16),
            int(match.group("size"), 16),
            int(match.group("max"), 16),
        )
        (loads if match.group("kind") == "Load" else execs).append(
            (match.group("name"), *item)
        )

    def limited(max_size: int) -> int:
        return 0 if max_size <= 0 or max_size >= _UNLIMITED else max_size

    regions = []
    loads = [item for item in loads if limited(item[3])]
    single_load = len(loads) == 1
    load_spans = [(base, base + max_size) for _, base, _, max_size in loads]
    for name, base, size, max_size in loads:
        regions.append(MemoryRegion(_arm_label(name, single_load, "Load"), size,
                                    limited(max_size)))

    groups: dict = {}
    for name, base, size, max_size in execs:
        capacity = limited(max_size)
        if not capacity:
            continue
        if any(start <= base < end for start, end in load_spans):
            continue  # 加载区的运行副本（ER_ROM），已由 FLASH 行覆盖
        entry = groups.setdefault((base, capacity), {"used": 0, "label": "RAM", "best": 0})
        entry["used"] += size
        if size >= entry["best"]:  # 同组里取实际占用的那个区的名字
            entry["best"] = size
            entry["label"] = _arm_label(name, single_load, "Execution")

    for (_, capacity), entry in groups.items():
        label = "RAM" if entry["label"] == "NOINIT" else entry["label"]
        regions.append(MemoryRegion(label, entry["used"], capacity))
    return regions


# ---------------------------------------------------------------- 统一入口


def collect_regions(compiler: str, map_path: str, output_text: str = "",
                    elf_path: str = "", build_dir: str = "") -> list:
    """按编译器选择解析方式；读不到链接产物时返回空列表。

    lld 的 ``-Map`` 不写 ``Memory Configuration``（区域上限）——它在链接时才打印，
    增量构建又没有链接输出——所以把上一次链接的结果缓存在构建目录里复用。
    """

    regions = _regions_from_output(compiler, map_path, output_text, elf_path)
    if regions:
        _save_cache(build_dir, regions)
        return sort_regions(regions)

    cached = _load_cache(build_dir, elf_path)
    return sort_regions(cached)


def _regions_from_output(compiler: str, map_path: str, output_text: str,
                         elf_path: str) -> list:
    map_text = _read_text(map_path)
    if not map_text:
        return []
    if compiler in ("gcc", "clang"):
        return gnu_regions(map_text, output_text, elf_path=elf_path)
    return parse_arm_regions(map_text)


def _cache_path(build_dir: str) -> str:
    return os.path.join(build_dir, CACHE_NAME) if build_dir else ""


def _save_cache(build_dir: str, regions: list) -> None:
    path = _cache_path(build_dir)
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([region.as_dict() for region in regions], handle, indent=2)
    except OSError:
        pass


def _load_cache(build_dir: str, elf_path: str) -> list:
    path = _cache_path(build_dir)
    if not path or not os.path.isfile(path):
        return []
    # 镜像比缓存新，说明是 omni 之外重新链接的，缓存不可信。
    try:
        if elf_path and os.path.isfile(elf_path) and \
                os.path.getmtime(elf_path) > os.path.getmtime(path):
            return []
    except OSError:
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    regions = []
    for row in data if isinstance(data, list) else []:
        try:
            regions.append(MemoryRegion(str(row["name"]), int(row["used"]),
                                        int(row["capacity"])))
        except (KeyError, TypeError, ValueError):
            continue
    return regions


def sort_regions(regions: Iterable) -> list:
    """容量大的排前面（FLASH 在前、RAM/CCM 在后），容量相同保持链接顺序。"""

    return sorted(regions, key=lambda region: -region.capacity)


# ---------------------------------------------------------------- 呈现


def format_size(value: int) -> str:
    """容量的人类可读写法：``1 MB`` / ``128 KB`` / ``512 B``。"""

    for unit, scale in (("GB", _UNITS["GB"]), ("MB", _UNITS["MB"]), ("KB", _UNITS["KB"])):
        if value >= scale:
            number = value / scale
            return f"{number:g} {unit}" if float(number).is_integer() else f"{number:.1f} {unit}"
    return f"{value} B"


def _usage_color(percent: float) -> str:
    if percent >= HIGH_USAGE:
        return RED
    if percent >= LOW_USAGE:
        return YELLOW
    return GREEN


def render_region(region, *, name_width: int, used_width: int, capacity_width: int,
                  palette: Optional[Palette] = None, bar_width: int = BAR_WIDTH) -> str:
    """画一行：``FLASH  [##...]  17672 B /  1 MB  1.69%``。"""

    used = f"{region.used} B"
    capacity = format_size(region.capacity)
    percent = min(region.percent, 999.99)
    if region.capacity > 0:
        ratio = min(region.used / region.capacity, 1.0)
        filled = int(ratio * bar_width + 0.5)  # 四舍五入，不用 banker's rounding
        if region.used > 0 and filled == 0:
            filled = 1  # 有占用就不画成空条
    else:
        filled = 0
    fill, rest = "#" * filled, "." * (bar_width - filled)
    bar = f"[{fill}{rest}]"
    percent_text = f"{percent:5.2f}%"
    if palette is not None and palette.enabled:
        color = _usage_color(percent)
        if fill:
            bar = f"[{palette(fill, color)}{rest}]"
        percent_text = palette(percent_text, color)
    return (
        f"{region.name:<{name_width}}  {bar}  "
        f"{used:>{used_width}} / {capacity:>{capacity_width}}  {percent_text}"
    )


def render_regions(regions: Iterable, palette: Optional[Palette] = None) -> list:
    """成批渲染（列宽在整批里对齐）；没有区域时返回空列表。"""

    rows = list(regions)
    if not rows:
        return []
    name_width = max(len(region.name) for region in rows)
    used_width = max(len(f"{region.used} B") for region in rows)
    capacity_width = max(len(format_size(region.capacity)) for region in rows)
    return [
        render_region(
            region,
            name_width=name_width,
            used_width=used_width,
            capacity_width=capacity_width,
            palette=palette,
        )
        for region in rows
    ]


def is_usage_header(line: str) -> bool:
    """实时日志里的 ``Memory region  Used Size ...`` 表头（终端里改画进度条）。"""

    return bool(USAGE_HEADER.match((line or "").strip()))


def is_usage_line(line: str) -> bool:
    """``--print-memory-usage`` 的数据行 / 块内空行。"""

    text = line or ""
    return bool(USAGE_ROW.match(text)) or not text.strip()
