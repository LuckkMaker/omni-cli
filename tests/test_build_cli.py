"""`omni build` 离线回归测试（不需要硬件/后端；默认不真正编译）。

用法::

    cd python
    python test_build_cli.py

设置 ``OMNI_BUILD_E2E=1`` 时会真的构建 template（需要 cmake/ninja/AC6）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PY_DIR)
from support import requires_backend  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError, ValueError):
        pass

from omni_cli.cli import build_parser
from omni_cli.context import ProjectError
from omni_cli.builder.report import (
    BuildReporter,
    compact_command,
    shorten_path,
    steps_from_log,
    summarize_failure,
)
from omni_cli.builder.runner import (
    ARTIFACTS,
    BuildResult,
    _cli_configure_args,
    build_directory,
    cache_conflicts,
    collect_artifacts,
    map_file,
    parse_size,
    read_cache_context,
    run_build,
    resolve_size_target,
    runner_argv,
    target_kind,
    toolchain_file,
)
from omni_cli.builder.memory import (
    MemoryRegion,
    format_size,
    gnu_regions,
    parse_arm_regions,
    parse_gnu_configuration,
    parse_gnu_sections,
    parse_gnu_usage,
    render_regions,
    sort_regions,
)
from omni_cli.formatting import Palette, color_enabled

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        failures.append(name)
        print(f"FAIL {name} {detail}")


MAP_TEXT = """\
Component: Arm Compiler for Embedded 6.23 Tool: armlink [5f102400]
Program Size: Code=6138 RO-data=582 RW-data=12 ZI-data=1652
"""


def workspace() -> Path | None:
    candidates = []
    env = os.environ.get("OMNI_WORKSPACE_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parents[1].parent / "omni-emb")
    for candidate in candidates:
        if (candidate / "omni" / "cmake").is_dir():
            return candidate
    return None


def test_target_kind() -> None:
    project = os.path.join("D:\\", "proj", "template")
    check("target.none", target_kind(None, project) == ("none", ""))
    check("target.clean", target_kind("clean", project) == ("clean", ""))
    check("target.size", target_kind("size", project) == ("size", ""),
          str(target_kind("size", project)))
    check("target.flash", target_kind("flash", project) == ("runner", ""))
    check("target.run", target_kind("run", project) == ("runner", ""))
    check("target.raw", target_kind("omni-size-template", project) ==
          ("cmake", "omni-size-template"))


SIZE_NINJA = """\
# CMAKE generated file: DO NOT EDIT!
build build.ninja: RERUN_CMAKE | CMakeCache.txt
build omni-size-app: phony CMakeFiles/omni-size-app omni.elf
build omni.elf: C_EXECUTABLE_LINKER__omni_elf_ CMakeFiles/app.dir/src/app.c.obj
"""


def test_resolve_size_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = os.path.join(tmp, "template")
        os.makedirs(project)
        check("size_target.missing_ninja",
              resolve_size_target(os.path.join(tmp, "no-build"), project)
              == "omni-size-template",
              resolve_size_target(os.path.join(tmp, "no-build"), project))

        build = os.path.join(tmp, "build")
        os.makedirs(build)
        with open(os.path.join(build, "build.ninja"), "w", encoding="utf-8") as handle:
            handle.write(SIZE_NINJA)
        # 工程目录名是 template，但 CMake 目标是 app：必须按 build.ninja 解析。
        check("size_target.from_ninja", resolve_size_target(build, project) == "omni-size-app",
              resolve_size_target(build, project))

        app_project = os.path.join(tmp, "app")
        os.makedirs(app_project)
        check("size_target.project_name", resolve_size_target(build, app_project)
              == "omni-size-app", resolve_size_target(build, app_project))

        with open(os.path.join(build, "build.ninja"), "a", encoding="utf-8") as handle:
            handle.write("build omni-size-boot: phony CMakeFiles/omni-size-boot omni-boot.elf\n")
        try:
            resolve_size_target(build, project)
        except ProjectError as exc:
            check("size_target.ambiguous", "omni-size-app" in str(exc)
                  and "omni-size-boot" in str(exc), str(exc))
        else:
            check("size_target.ambiguous", False, "多目标未报错")

        with open(os.path.join(build, "build.ninja"), "w", encoding="utf-8") as handle:
            handle.write("build omni.elf: phony\n")
        check("size_target.no_size_target", resolve_size_target(build, project)
              == "omni-size-template", resolve_size_target(build, project))


def test_runner_argv() -> None:
    argv = runner_argv("flash", "/x/omni.axf", ["--no-verify"])
    check("runner.argv", argv[-4:] == ["flash", "program", "/x/omni.axf", "--no-verify"],
          str(argv))
    check("runner.base", bool(argv[0]), str(argv))


def test_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(["build", "-b", "stm32f407ig"])
    check("parser.board", args.board == "stm32f407ig")
    check("parser.compiler", args.compiler == "ac6")
    check("parser.build_type", args.build_type == "Debug")
    args = parser.parse_args(["build", "-b", "x", "-c", "gcc", "--build-type", "Release",
                              "--clean", "--configure-only", "--cmake", "-t", "omni-size-template",
                              "--cmake-arg=-DFOO=1", "-j", "8", "--json"])
    check("parser.options", args.compiler == "gcc" and args.build_type == "Release"
          and args.clean and args.configure_only and args.cmake and args.jobs == 8
          and args.json_ and args.target == "omni-size-template"
          and args.cmake_args == ["-DFOO=1"], str(args))


def test_cache_context() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "CMakeCache.txt")
        with open(cache_path, "w", encoding="utf-8") as handle:
            handle.write("// comment\n")
            handle.write("OMNI_BOARD:STRING=stm32f407ig\n")
            handle.write("OMNI_COMPILER:STRING=ac6\n")
            handle.write("CMAKE_BUILD_TYPE:STRING=Debug\n")
            handle.write("OTHER:STRING=x\n")
        cache = read_cache_context(tmp)
        check("cache.read", cache == {"board": "stm32f407ig", "compiler": "ac6",
                                      "build_type": "Debug"}, str(cache))
        check("cache.same", cache_conflicts(cache, "stm32f407ig", "ac6", "Debug") == [])
        conflicts = cache_conflicts(cache, "stm32h743ii", "ac6", "Debug")
        check("cache.conflict", len(conflicts) == 1 and "board" in conflicts[0], str(conflicts))
        check("cache.empty", read_cache_context(os.path.join(tmp, "nope")) == {})


def test_build_directory() -> None:
    path = build_directory(r"D:\proj", "stm32f407ig", "ac6", "Debug")
    check("dir.shape", path.replace("\\", "/").endswith("proj/build/stm32f407ig/AC6/Debug"), path)
    path = build_directory("/proj", "b", "gcc", "Release")
    check("dir.upper", "/proj/build/b/GCC/Release" in path.replace("\\", "/"), path)


def test_toolchain() -> None:
    root = workspace()
    if not root:
        print("skip toolchain (workspace not found)")
        return
    omni_root = str(root / "omni")
    ac6 = toolchain_file(omni_root, "ac6")
    check("toolchain.ac6", ac6.endswith("armclang6.cmake") and os.path.isfile(ac6), ac6)
    gcc = toolchain_file(omni_root, "gcc")
    check("toolchain.gcc", gcc.endswith("gcc.cmake") and os.path.isfile(gcc), gcc)
    try:
        toolchain_file(omni_root, "ac5")
    except ProjectError as exc:
        check("toolchain.ac5", "尚未提供" in str(exc))
    else:
        check("toolchain.ac5", False, "ac5 未报错")


def test_parse_size_and_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        map_path = os.path.join(tmp, "omni.axf.map")
        with open(map_path, "w", encoding="utf-8") as handle:
            handle.write(MAP_TEXT)
        size = parse_size(map_path)
        check("size.parsed", size == {"code": 6138, "ro_data": 582, "rw_data": 12, "zi_data": 1652}, str(size))
        check("size.missing", parse_size(os.path.join(tmp, "nope.map")) is None)

        for name in ("omni.axf", "omni.hex"):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                handle.write("x")
        found = collect_artifacts(tmp)
        check("artifacts.subset", {"omni.axf", "omni.hex"} <= set(found), str(sorted(found)))
        check("artifacts.list", "omni.axf.map" in ARTIFACTS and "omni.elf" in ARTIFACTS)
        check("artifacts.map", "omni.elf.map" in ARTIFACTS)
        check("map.file", map_file("ac6") == "omni.axf.map" and map_file("gcc") == "omni.elf.map"
              and map_file("clang") == "omni.elf.map", map_file("gcc"))


GNU_MAP_TEXT = """\
Memory Configuration

Name             Origin             Length             Attributes
CCMRAM           0x10000000         0x00010000         xrw
RAM              0x20000000         0x00020000         xrw
FLASH            0x08000000         0x00100000         xr
*default*        0x00000000         0xffffffff

Linker script and memory map

.vectors        0x08000000      0x188
.text           0x08000188     0x1970
.data           0x20000000        0xc load address 0x08001ba8
.bss            0x2000000c       0x30
._user_heap_stack
                0x2000003c      0x604
"""

#: GNU ld：每行前后各一个空行。
LD_USAGE_TEXT = """\
Memory region         Used Size  Region Size  %age Used

           CCMRAM:           0 B        64 KB      0.00%

             RAM:        4544 B       128 KB      3.47%

           FLASH:       17672 B         1 MB      1.69%

"""

#: llvm lld：没有空行，0 写成 0 GB。
LLD_USAGE_TEXT = """\
Memory region         Used Size  Region Size  %age Used
          CCMRAM:          0 GB        64 KB      0.00%
             RAM:        1568 B       128 KB      1.20%
           FLASH:        7016 B         1 MB      0.67%
"""

ARM_MAP_TEXT = """\
Component: Arm Compiler for Embedded 6.23 Tool: armlink [5f102400]

Memory Map of the image

  Load Region LR_ROM (Base: 0x08000000, Size: 0x00001990, Max: 0x00100000, ABSOLUTE)

    Execution Region ER_ROM (Base: 0x08000000, Size: 0x00001980, Max: 0x00100000, ABSOLUTE)

    0x08000000   0x00000188   Data   RO            3    RESET               startup.o

    Execution Region RW_NOINIT (Base: 0x20000000, Size: 0x00000000, Max: 0x00020000, ABSOLUTE, UNINIT)

    Execution Region RW_RAM (Base: 0x20000000, Size: 0x00000428, Max: 0x00020000, ABSOLUTE)

Program Size: Code=6138 RO-data=582 RW-data=12 ZI-data=1652
"""


def test_memory_regions() -> None:
    usage = parse_gnu_usage(LD_USAGE_TEXT)
    check("memory.ld_rows", [(r.name, r.used, r.capacity) for r in usage] ==
          [("CCMRAM", 0, 65536), ("RAM", 4544, 131072), ("FLASH", 17672, 1048576)],
          str(usage))
    lld = parse_gnu_usage(LLD_USAGE_TEXT)
    check("memory.lld_rows", [(r.name, r.used) for r in lld] ==
          [("CCMRAM", 0), ("RAM", 1568), ("FLASH", 7016)], str(lld))

    config = parse_gnu_configuration(GNU_MAP_TEXT)
    check("memory.config", [(name, origin, size) for name, origin, size in config] ==
          [("CCMRAM", 0x10000000, 65536), ("RAM", 0x20000000, 131072),
           ("FLASH", 0x08000000, 1048576)], str(config))

    sections = parse_gnu_sections(GNU_MAP_TEXT)
    check("memory.sections", sections == [
        (0x08000000, 0x188, None),
        (0x08000188, 0x1970, None),
        (0x20000000, 0xC, 0x08001BA8),
        (0x2000000C, 0x30, None),
        (0x2000003C, 0x604, None),
    ], str(sections))

    # 上限取 map 的精确值；已用取链接器自报的值。
    merged = gnu_regions(GNU_MAP_TEXT, LD_USAGE_TEXT)
    check("memory.merged", [(r.name, r.used, r.capacity) for r in merged] ==
          [("CCMRAM", 0, 65536), ("RAM", 4544, 131072), ("FLASH", 17672, 1048576)],
          str(merged))

    # 没有链接输出（增量构建）时按段地址求和，.bss 的 LMA 不能算进 Flash。
    fallback = gnu_regions(GNU_MAP_TEXT)
    totals = {r.name: r.used for r in fallback}
    check("memory.fallback", totals == {
        "FLASH": 0x188 + 0x1970 + 0xC,
        "RAM": 0xC + 0x30 + 0x604,
        "CCMRAM": 0,
    }, str(totals))

    arm = parse_arm_regions(ARM_MAP_TEXT)
    check("memory.arm", [(r.name, r.used, r.capacity) for r in arm] ==
          [("FLASH", 0x1990, 0x100000), ("RAM", 0x428, 0x20000)], str(arm))

    ordered = sort_regions([MemoryRegion("RAM", 1, 20480),
                            MemoryRegion("FLASH", 1, 131072)])
    check("memory.sorted", [r.name for r in ordered] == ["FLASH", "RAM"],
          str(ordered))

    check("memory.format_size",
          (format_size(1048576), format_size(131072), format_size(512),
           format_size(1536)) == ("1 MB", "128 KB", "512 B", "1.5 KB"),
          str([format_size(v) for v in (1048576, 131072, 512, 1536)]))

    rows = render_regions([MemoryRegion("FLASH", 17672, 1048576),
                           MemoryRegion("RAM", 4544, 131072),
                           MemoryRegion("CCMRAM", 0, 65536)])
    check("memory.render_lines", len(rows) == 3, str(rows))
    check("memory.render_flash", rows[0] ==
          "FLASH   [#...................]  17672 B /   1 MB   1.69%", repr(rows[0]))
    check("memory.render_zero", rows[2] ==
          "CCMRAM  [....................]      0 B /  64 KB   0.00%", repr(rows[2]))
    check("memory.render_bar_width", all(len(row.split("[")[1].split("]")[0]) == 20
                                         for row in rows), str(rows))
    tiny = render_regions([MemoryRegion("FLASH", 1, 1048576)])[0]
    check("memory.render_tiny", "#" in tiny and tiny.count("#") == 1, repr(tiny))
    over = render_regions([MemoryRegion("RAM", 4096, 1024)])[0]
    check("memory.render_over", over.count("#") == 20 and "400.00%" in over, repr(over))


def test_memory_reporter() -> None:
    import contextlib
    import io

    reporter = BuildReporter(Palette(False))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        for line in (LD_USAGE_TEXT + "构建完成  2 步  0.2s").splitlines():
            reporter.line(line)
    text = buffer.getvalue()
    check("memory.report_swallow", "Memory region" not in text and "FLASH" not in text,
          repr(text))
    check("memory.report_keep", "构建完成" in text, repr(text))


def test_success_summary() -> None:
    """结尾必须同时给区域进度条和段级细账（0.45.0 曾把细账藏进 -v，0.45.1 恢复）。"""

    import contextlib
    import io

    result = BuildResult(
        project_dir="D:/proj",
        board="stm32f407ig",
        compiler="ac6",
        build_type="Debug",
        build_dir="D:/proj/build/stm32f407ig/AC6/Debug",
        toolchain="armclang6.cmake",
        success=True,
        built=True,
        artifacts={"omni.axf": "D:/proj/build/omni.axf"},
        size={"code": 29052, "ro_data": 1004, "rw_data": 116, "zi_data": 9076},
        memory_regions=[
            MemoryRegion("FLASH", 30172, 1048576),
            MemoryRegion("RAM", 9192, 131072),
        ],
    )
    for verbose, expected_size in ((False, True), (True, True)):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            BuildReporter(Palette(False), verbose=verbose).success(result)
        text = buffer.getvalue()
        check(f"summary.bars.{verbose}", "FLASH  [#" in text and "RAM    [#" in text,
              repr(text))
        check(f"summary.size.{verbose}",
              ("Code=29052  RO-data=1004  RW-data=116  ZI-data=9076" in text) is expected_size,
              repr(text))
        check(f"summary.artifact.{verbose}", "omni.axf" in text, repr(text))


def test_report_helpers() -> None:
    configure = [
        "cmake", "-S", "D:/proj", "-B", "D:/proj/build/stm32f407ig/AC6/Debug",
        "-G", "Ninja", "-DOMNI_BOARD=stm32f407ig",
    ]
    check("report.compact_configure",
          compact_command(configure, "configure") ==
          "cmake -S D:/proj -B D:/proj/build/stm32f407ig/AC6/Debug -G Ninja",
          compact_command(configure, "configure"))

    build = ["cmake", "--build", "D:/proj/build", "--target", "omni-size-x", "--parallel", "8"]
    check("report.compact_build",
          compact_command(build, "build") ==
          "cmake --build D:/proj/build --target omni-size-x --parallel 8",
          compact_command(build, "build"))

    check("report.shorten", shorten_path(r"D:\ws\omni-emb\modules\a.h", r"D:\ws\omni-emb")
          == "modules\\a.h" or shorten_path(r"D:\ws\omni-emb\modules\a.h", r"D:\ws\omni-emb")
          == "modules/a.h", shorten_path(r"D:\ws\omni-emb\modules\a.h", r"D:\ws\omni-emb"))
    check("report.shorten_outside",
          shorten_path(r"D:\other\a.h", r"D:\ws") == r"D:\other\a.h")

    sample = "\n".join([
        "[1/129] Building C object a.o",
        "FAILED: obj/a.o",
        "D:/ws/modules/x/include/y.h:32:10: fatal error: 'Z.h' file not found",
        "[2/129] Building C object b.o",
        "FAILED: obj/a.o",  # 重打日志里重复的目标
        "D:/ws/modules/x/include/y.h:32:10: fatal error: 'Z.h' file not found",
    ])
    summary = summarize_failure(sample)
    check("report.failed_targets", summary["targets"] == 1, str(summary))
    check("report.first_error", summary["first_error"] and summary["first_error"].endswith(
        "fatal error: 'Z.h' file not found"), str(summary))
    check("report.steps", steps_from_log(sample) == 129, str(steps_from_log(sample)))


def test_color() -> None:
    check("color.never", color_enabled("never") is False)
    check("color.always", color_enabled("always") is True)

    old = os.environ.get("NO_COLOR")
    os.environ["NO_COLOR"] = "1"
    try:
        check("color.no_color", color_enabled("auto") is False)
    finally:
        if old is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = old

    plain = Palette(False)("x", "\033[31m")
    check("color.palette_off", plain == "x", repr(plain))
    painted = Palette(True)("x", "\033[31m")
    check("color.palette_on", painted.startswith("\033[31m") and painted.endswith("\033[0m"),
          repr(painted))


def test_cli_args() -> None:
    args = _cli_configure_args()
    check("cli_args.nonempty", bool(args), str(args))
    joined = " ".join(args)
    check(
        "cli_args.shape",
        joined.startswith("-DOMNI_CLI=") or
        ("-DOMNI_CLI_PYTHON=" in joined and "-DOMNI_CLI_MODULE=omni_cli" in joined),
        joined,
    )


def test_e2e() -> None:
    if os.environ.get("OMNI_BUILD_E2E") != "1":
        print("skip e2e build (set OMNI_BUILD_E2E=1 to enable)")
        return
    root = workspace()
    if not root:
        print("skip e2e build (workspace not found)")
        return
    project = root / "examples" / "basic" / "template"
    result = run_build(
        project_dir=str(project),
        omni_root=str(root / "omni"),
        board="stm32f407ig",
        compiler="ac6",
        build_type="Debug",
    )
    check("e2e.success", result.success, f"exit={result.exit_code}")
    check("e2e.size", result.size is not None and result.size["code"] > 0, str(result.size))
    check("e2e.memory_regions", bool(result.memory_regions) and
          result.memory_regions[0].capacity > 0, str(result.memory_regions))
    check("e2e.memory_json", "memory_regions" in result.as_dict()
          and result.as_dict()["memory_regions"][0]["name"] == result.memory_regions[0].name,
          str(result.as_dict().get("memory_regions")))
    check("e2e.artifacts", "omni.axf" in result.artifacts, str(sorted(result.artifacts)))
    check("e2e.log_path", bool(result.log_path) and os.path.isfile(result.log_path),
          str(result.log_path))
    if result.log_path:
        with open(result.log_path, encoding="utf-8", errors="replace") as handle:
            raw_log = handle.read()
        check("e2e.log_no_ansi", "\033[" not in raw_log)
    check("e2e.steps", result.steps > 0, str(result.steps))
    check("e2e.timing", result.build_seconds > 0, str(result.build_seconds))
    check("e2e.no_failure", result.failed_targets == 0 and result.first_error is None,
          f"{result.failed_targets} / {result.first_error}")

    # 第二次构建应跳过 configure（构建目录已配置），强制时才重新配置。
    again = run_build(
        project_dir=str(project),
        omni_root=str(root / "omni"),
        board="stm32f407ig",
        compiler="ac6",
        build_type="Debug",
    )
    check("e2e.skip_configure", again.success and not again.configured, str(again.configured))
    forced = run_build(
        project_dir=str(project),
        omni_root=str(root / "omni"),
        board="stm32f407ig",
        compiler="ac6",
        build_type="Debug",
        force_configure=True,
    )
    check("e2e.force_configure", forced.success and forced.configured, str(forced.configured))

    # -t size：目标名要按 build.ninja 解析（工程名 template ≠ 可执行目标 app）。
    sized = run_build(
        project_dir=str(project),
        omni_root=str(root / "omni"),
        board="stm32f407ig",
        compiler="ac6",
        build_type="Debug",
        target="size",
    )
    check("e2e.size_target", sized.success and sized.built, f"exit={sized.exit_code}")
    check("e2e.size_target_regions", bool(sized.memory_regions), str(sized.memory_regions))


def main() -> int:
    test_parser()
    test_target_kind()
    test_resolve_size_target()
    test_runner_argv()
    test_cache_context()
    test_build_directory()
    test_toolchain()
    test_parse_size_and_artifacts()
    test_memory_regions()
    test_memory_reporter()
    test_success_summary()
    test_report_helpers()
    test_color()
    test_cli_args()
    test_e2e()
    if failures:
        print(f"{len(failures)} failure(s): {', '.join(failures)}")
        return 1
    print("all build tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
