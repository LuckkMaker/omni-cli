# 版本记录

> `omni` CLI 的完整版本历史（从 0.1.0 起），从 README 迁出。
> 安装、命令参考与测试见 [README](../README.md)。

## 未发布

- CLI 呈现统一到 `python -h` / `pip -h` 的风格：帮助只做缩进对齐与按显示宽度折行，
  不加颜色和符号装饰；分组标题改用英文（`Backend` / `Debug & Flash` / `Project Build` /
  `Serial / Network / USB`），栏目名为 `commands:`，示例块统一为 `Examples:`。
- 示例块改用 `RawDescriptionHelpFormatter` 渲染，保持逐行排版（此前会被 argparse 压成一段）。
- 帮助文案统一：`缺省` 改 `默认`、`枚举` 改 `列出`、去掉与 `--timeout` 重复的逐命令超时说明、
  统一「查询状态」「开启/关闭」等句式。
- `omni build` 输出去掉 `›` / `✓` / `✗` / `·` 等装饰，结论行改为纯文本（着色仍由 `--color` 控制）。
- `omni usb capture open` 的解析进度只在终端原地刷新，管道/重定向下不输出。

## 0.28.0（USB 能力）

- 新增 `omni usb` 命令组：诊断、抓包、描述符、请求、回归用例。
- 抓包：`doctor`、`capture start|stop|status|transfers(--follow)|detail|devices|open|save`。
- 描述符：`desc parse|validate|build|emit|template|from-capture`（hex / JSON）。
- 请求：`usb cdc`（复用串口，含 BREAK）、`usb hid`（报告级）、
  `usb winusb`（控制/批量/中断、pipe 控制、echo、list --all、--capture 联动）。
- 测试：`usb test run|list`，支持离线 pcap 断言与真机动作步骤，输出 JSON / JUnit。
- `serial` 新增 `break` 端点；顶层命令分组改为「串口 / 网络 / USB」。

## 0.24.0（独立项目首版）

- 从 OMNI Link 仓库（`python/omni_cli/`）拆分为独立仓库，git 历史随迁。
- 目录改为标准 Python 布局：包在 `omni_cli/`，测试在 `tests/`，打包/发布脚本在 `scripts/`。
- 后端定位不再假定"包目录上两级是 omni-link"：环境变量 → 持久化配置 → 仓库布局探测（含 submodule 布局）。
- 运行时文件（`.backend.log` / `.backend_runtime.json` / `cli.config.json`）统一写用户级目录，避免弄脏 submodule。
- 新增 `scripts/build-exe.ps1`（PyInstaller 单文件 exe），`cli-release.ps1` 改为调用它。
- `omni backend status` 输出后端 `protocol`；后端协议高于本 CLI 支持版本时给出一条升级提示。

## 0.22.0（未发布）

- **移除离线工具链打包**：不再产出 `omni-toolchain.exe`，也不再附带 `toolchains/`、
  `tools/`、`scripts/build-toolchain-offline.ps1`、`scripts/prepare-tools.ps1`。
  构建工具（cmake / ninja / 编译器）改由用户环境提供：显式覆盖环境变量 →
  可选工具目录（存在才生效）→ 系统 `PATH`；`omni toolchain` 仍可查看解析结果。
- 安装包与 `package.json` 同步去掉 `toolchain:package` / `package:with-tools`，
  以及 `toolchain/omni-toolchain.exe` 这项 extraResources。

## 0.21.0（2026-10-23）

- `omni build` 输出重做（仍然逐行实时转发，不缓冲、不过滤）：
  - 头部 3 行给出工程/板·编译器·类型/构建目录，配置与构建命令显示缩写（`-v` 打全量）；
  - 结尾给结论：成功 = 步数 + 耗时 + `Program Size` + 产物；失败 = exit + 失败目标数 + 首个错误 + 日志路径；
  - **修复重复**：失败时不再把已经流式打印过的整份日志往 stderr 重打一遍（实测每次多 41 KB）；
  - 新增 `--color auto|always|never`（TTY 自动着色，`NO_COLOR`/`OMNI_CLI_COLOR` 可覆盖），
    Windows 控制台自动开启 VT；
  - 全量原始日志落盘 `<build-dir>/build.log`（无 ANSI），`--json` 新增
    `configure_seconds` / `build_seconds` / `log_path` / `steps` / `failed_targets` / `first_error` / `failed_phase`。
- `-v` 恢复语义：显示完整命令；此前 `stream_build` 恒为真，`-v` 实际无效。
- 修复源码运行时 `python -m omni_cli` 的 PYTHONPATH 继承问题：configure 阶段
  （CMake 在构建目录里起子进程）不再报 `No module named omni_cli`。
- 新增 `omni toolchain`（`--json`）：显示 cmake / ninja / clang / gcc / cbuild
  的解析来源。安装目录的 `tools/` 默认前插到 `PATH`，缺失时回退系统环境；
  显式覆盖用 `OMNI_CMAKE` / `OMNI_NINJA` / `OMNI_CLANG` / `OMNI_GCC` / `OMNI_CBUILD`。
- `-t flash/run` 同时接受 `omni.axf`（AC6）与 `omni.elf`（GCC/CLANG）。
- 新增独立 `omni-toolchain.exe`（离线工具链安装器）与 `omni toolchain` 的
  `env` / `shared` / `app` / `system` 来源显示；工具链默认装到
  `%LOCALAPPDATA%\OMNI\toolchains`，不再塞进主安装包。

## 0.16.0（2026-10-22）

- `omni config bsp` 的 map 自动发现优先看 `build/<board>/**/*.map`：此前会抓到 `Listings/`、`Objects/`、`out/` 下的陈旧/其它板的 map，把不属于本板的设备实例写进 `bsp_config.h`（例如 f103cb 里多出 `_dev_gpioe`）。
  设备来源的正则本身没问题，`GPIO_DEVICE_DEFINE(gpiob, GPIOB)` 能正确解析成 `_dev_gpiob`。
## 0.15.0（2026-10-22）

- `omni build` 支持 GCC 产物：`omni.elf`（GCC/CLANG）与 `omni.axf`（AC5/AC6）并存识别；
  GCC 侧没有 armlink 的 `Program Size`，改从 `omni.size.txt`（`arm-none-eabi-size`）
  解析 `gnu_size`（text/data/bss）并在输出与 `--json` 里给出。
## 0.14.0（2026-10-22）

- `omni config bsp` 生成的 `bsp_config.h` 内容稳定化：注释不再写入「来自 linker map /
  源码扫描」这类随构建状态变化的信息，避免 configure 期（map 还不存在）与构建后
  生成不同字节、造成无意义 diff。来源信息仍打印在日志里。
## 0.13.0（2026-10-22）

- `omni build -t` 目标体系（对齐 `west build -t`）：
  - `-t size` → 构建 `omni-size-<project>` 目标；
  - `-t clean` → 等价 `--clean`；
  - `-t flash` / `-t run` → 构建完成后调用 `omni flash program <omni.axf>`（`--runner-arg` 透传参数）；
  - 其它值 → 原样透传给 `cmake --build --target`。
- runner 与 CMake 配置统一使用「当前 CLI」（PATH 上版本更低时自动退回 `python -m omni_cli`）。
## 0.12.0（2026-10-22）

- `omni build` 对齐 west 的行为：
  - **默认跳过 CMake 配置**（构建目录已有 `CMakeCache.txt` 时），`--cmake` 强制重新配置；
  - 构建目录与上下文绑定：cache 里的 board/compiler/build-type 不一致时报错并提示 `--clean`；
  - 新增 `-t/--target`（只构建指定目标，如 `omni-size-template`）；
  - 新增 `-o/--cmake-arg`（透传 cmake 参数，注意写成 `--cmake-arg=-DFOO=1`）；
  - `-t` 让给 `--target`，构建类型改用 `--build-type`。
## 0.11.0（2026-10-22）

- 新增 `omni build`：本地 CMake 配置 + 构建（Ninja），产物路径与尺寸直接返回：
  - 构建目录固定 `<project>/build/<board>/<COMPILER>/<buildtype>`；
  - `--clean` / `--configure-only` / `-j` / `--json` / `-v`；
  - 自动把「当前 CLI」传给 CMake（`-DOMNI_CLI` 或 `-DOMNI_CLI_PYTHON`+`-DOMNI_CLI_MODULE`），
    不再依赖 `PATH` 上的 omni 版本。
- `omni config bsp` 的 map 自动发现增加 `build/**/*.map`（CMake 产物目录）。
## 0.10.0（2026-10-22）

- 新增 `omni modules`：`list` / `check` / `generate`（本地命令，不依赖后端）：
  - 扫描 `modules/<dir>/omni/module.yml` 与 `omni/modules/<name>/module.yml`
    （后者对应 Zephyr 的 `cmake-ext`，用于上游镜像仓库）；
  - `generate` 写出 `omni/cmake/omni_modules.cmake` 与 `omni/Kconfig.modules`，
    `--check` 校验生成物是否漂移（不一致返回 2）。
## 0.9.0（2026-10-22）

- 新增 `omni boards`：`list` / `show` / `check` / `sync`（本地命令，不依赖后端）：
  - 板事实来自 `omni/boards/<board>/board.yml`；
  - `check` 比对工程 `boards/<board>/startup/*` 与 HAL 模块里的原始文件；
  - `sync` 只补缺失文件，默认不覆盖已有文件，可生成 HAL 配置头模板。
- 新增依赖 `PyYAML>=6.0`。
- README：修正 `project` 段标题为 `config`。
## 0.8.0（2026-10-22）

- 新增 `omni file`：
  - `info`：解析 bin/hex/elf 的格式、大小、段信息；
  - `checksum`：本地计算 modbus-crc16 / crc16-ccitt / crc32 / add8 / xor8 / add16，
    支持 `--start` / `--end` 字节范围。
- 新增 `omni flash compare <file>`：对比目标 Flash/RAM 与固件文件，返回首个不一致地址、
  差异总数与差异列表（`--max-diffs` / `--fail-fast`），不写目标。
- 新增 `omni tool map <file.map>`：解析 ARM 链接器 map，输出 ROM/RAM 使用、区域、
  分类与 ROM Top 条目。
- 后端新增 `POST /api/probes/{uid}/flash/compare`；Flash 地址对比前自动
  `reset_and_halt`，避免读到陈旧 Flash。

## 0.7.0（2026-10-21）

- **破坏性变更**：`omni project` 重命名为 `omni config`，子命令 `generate`（默认）/ `menuconfig` / `bsp` / `sync-keil`；不保留旧命令。
- **破坏性变更**：板级选项 `--target` 改为 `-b/--board`；`probe connect --target` 仍表示芯片型号，未变。
- **破坏性变更**：环境变量 `OMNI_TARGET` 改为 `OMNI_BOARD`。
- 包结构：`omni_cli.project` → `omni_cli.config`；`context` 上提为 `omni_cli.context`。
- 命令注册改为单一来源（`commands.add_all_parsers`），新增命令不必再改 `cli.py`。
- 启动器路径（omni-emb 侧）：`omni/scripts/omni_project/omni_project.py` → `omni/scripts/omni_config/omni_config.py`。

## 0.6.0（2026-10-21）

- 新增运维与设备信息命令：
  - `omni system info`（后端/数据库/Python/pyOCD 版本与设备库统计）；
  - `omni commander reset|cancel`；
  - `omni probe jlink-devices`（J-Link 设备库检索，配合 `--jlink-device`）；
  - `omni device flm-info <file.FLM>`（自动提取 Flash 参数供 `device custom`）；
  - `omni rtt send --file <path>`（发送文件原始字节）。

## 0.5.0（2026-10-21）

- 新增 `omni monitor`：ELF/符号、监视变量增删改、采样 start/stop/pause/resume、
  目标内核 run/halt/reset/state、历史读取（`read`，支持 `--follow`）与 CSV 导出。
- `monitor var add --symbol` 自动解析符号地址/类型；数组必须 `--index`，越界/歧义会拒绝。
- `SessionRecorder.read_range` 读取前 flush 当前文件，采样进行中即可读到最新样本，
  支撑 `monitor read --follow`。

## 0.4.0（2026-10-21）

- 新增 **临时挂包**：`omni probe connect <uid> -t <part> --pack <file.pack>`（可重复）。
  行为对齐 pyOCD `--pack`：只注册到当前后端进程的 `TARGET`，不复制文件、不写设备库，
  `backend stop/restart` 后失效；同一后端内的后续命令无需重复 `--pack`。
- 新增 `omni pack` 命令组：`list(show)` / `preview` / `import` / `remove`，复用 GUI
  「设置 → 芯片管理」的 `/api/packs` 能力。
- `probe connect` 增加 pyOCD 风格别名：`-t/--target`、`-f/--frequency`（支持 `4m`、
  `2.5khz`）、`-M/--connect`；`probe target --pack` 可在切换目标时挂包。
- Pack 生命周期修正：卸载只删除本 Pack 拥有的 `TARGET` 键，内置型号与其它 Pack 的
  同名型号不被误删；安装清单路径失效时按 filename 从 `packs/` 自愈；`device list`
  不再把 Pack 芯片误标为“不可烧录”。
- 新增能力缺口评审：`docs/tech/cli-coverage.md`（pyOCD CLI 与应用端 REST/UI 对照）。

## 0.3.2（2026-10-21）

- `omni project` 的人类可读成功摘要改为 ASCII/英文（`Project configuration generated.`
  等），避免在 Keil 的 GBK 控制台里显示为乱码；错误前缀也改为 `Error:`。
- 配套修复 CMSIS `execute`：项目目录改为通过 `$input(1)$` 传递，避免
  `$ProjectDir$` 在 `run` 中展开为空导致 `--project-dir: expected one argument`。
- 启动器最低版本提升到 `0.3.2`。

## 0.3.1（2026-10-21）

- 修复 `omni project sync-keil` 的 FatFs 规则：`CONFIG_FATFS` → `CONFIG_FS_FATFS`
  （当前 omni 框架使用 `FS_FATFS`，旧符号导致 `fatfs_arch.c` 不会同步进 Keil 工程）。
- 修复 `omni project menuconfig` 在找不到 `menuconfig` 命令时遗留临时
  `omni-app-*.Kconfig` 的问题。
- 补充 `FS_FATFS`、`--stop-on-change`、`menuconfig` 清理的离线回归测试。

## 0.3.0（2026-10-21）

- 产品名统一为 **OMNI CLI**；Python 包 `omnilink_cli` → `omni_cli`。
  发行名 `omni-cli` 和命令 `omni` 不变；开发态调用改为 `python -m omni_cli`。
- 新增本地命令 `omni project`：
  - `config`：Kconfig 合并、`omni_config.h`、`bsp_config.h`，可选 MSP / Keil 同步；
  - `menuconfig`：交互式配置；
  - `bsp`：独立生成 `bsp_config.h`；
  - `sync-keil`：按已有 `.config` 同步 Keil 工程，默认全部 `*.uvprojx`。
- 工程配置逻辑从 `omni-emb/omni/scripts` 重新实现到 CLI；旧脚本保留作参考/备份，
  不再被 CLI 调用。
- 新增 `kconfiglib` 依赖（`config` / `menuconfig`）；`bsp` / `sync-keil` 不需要它。
- 新增 `omni-emb/omni/scripts/omni_project/omni_project.py` 薄启动器，供 Keil/CMSIS 构建钩子调用。

## 0.2.3（2026-10-20）

- 修复 `rtt read --since <过期游标> --json` 的 stdout 被中文警告污染、无法被
  `ConvertFrom-Json` 解析的问题：`--json` 模式不再向 stderr 输出 `session_reset`/`lost`
  警告，改为在 JSON 响应中增加 `warning` 字段；stdout 始终是纯 JSON/JSONL，人类可读模式
  的警告仍在 stderr。
- `rtt read --json --out FILE` 不再输出“已追加写入”stderr 提示，避免合并捕获时污染 JSON。

## 0.2.2（2026-10-20）

- 修复 `flash erase --type chip` 后 `program` 内部校验误报 `Verify failed at 0x08000000`：
  Flash 算法写完后调试口在复位前可能读到陈旧内容。`program` 的内部校验与独立
  `flash verify` 现在先 `reset_and_halt` 再读取；`erase` / `fill`（Flash）结束也会复位一次。
- 修复 `flash fill`：导入路径改为 `pyocd.flash.loader`（原 `flash_loader` 不存在）；
  Flash 填充使用 `chip_erase="sector"`，并校验范围落在单个 region 内。
- `flash fill --value` 支持 `0x55` 等十六进制写法（仍限制 0-255）。
- RTT `--since`：`seq` 改为在同一后端进程内跨 `rtt start` 单调递增（只清缓存、不重置编号），
  旧会话的 `next_seq` 可继续作为游标；后端重启后才从 1 开始，并返回 `session_reset` 提示，
  不再静默返回 0 字节。
- `probe list` 新增 `--refresh`；文档说明其状态来自 1~2s 监控快照，连接权威状态用
  `probe status`。
- 文档明确 `flash reset`（默认复位后运行）与 `debug reset`（默认复位并暂停）的默认值相反。

## 0.2.1（2026-10-20）

- Flash 操作前自动停止 RTT 轮询（Flash 算法需要独占 SWD）：修复 RTT 会话运行时全片擦除
  被 30s 超时掐断、重试排队超时的问题；响应中的 `rtt_stopped` 告知是否自动停止。
- `flash program` / `erase` 默认 HTTP 超时提高到 **180s**；`verify` / `blank-check` /
  `read` / `fill` 为 120s（仍可用 `--timeout` 覆盖）。
- `flash erase --json` 返回 `bytes_erased`，不再返回误导性的 `bytes_written: 0`；
  各操作只输出本操作有意义的计数字段。
- 同一探针的 Flash 操作串行化：重复请求立即 409，避免客户端超时后并发擦除/烧录。
- 修复 `debug halt` 后立即 `debug regs` 的偶发竞态：RTT 轮询与寄存器读取共享 target
  （现通过调试协调锁串行），且 Zone 的 halt 不再被 RTT 自动 resume 覆盖。
- 文档修正：`commander exec list` 是 pyOCD 的探针/目标列表，列出 pyOCD 命令应使用
  `commander exec help`。

## 0.2.0（2026-10-20）

- **新增命令 `omni rtt read`**：读取 SEGGER RTT 上行数据。支持 `--follow`（持续跟踪）、
  `--duration`、`--out`（原始字节落盘）、`--channel`、`--since`、`--json`（follow 为 JSONL）。
- `probe connect`：`--target` 改为必填（帮助与 argparse 双重标注）；`--speed` 校验
  5 kHz ~ 50 MHz 并给出 `Hz/kHz` 单位提示；连接失败回落到 `disconnected`，修正参数后可直接重试。
- `commander exec`：命令之后出现的已知全局选项（`--uid/--backend-port/--timeout/--managed/--json/--quiet`）
  自动前移，`--json` 在任意位置均可用。
- `flash verify --json`：不再返回误导性的 `bytes_written`，改为 `bytes_verified`。
- `debug regs` / `debug status`：会话失效时返回 `success:false` 与非零退出码；
  寄存器接口区分“目标运行中（409，提示先 halt）”与“会话失效（503）”。
- 修复管道/重定向下中文输出 `�`（U+FFFD）：CLI stdout/stderr 显式固定 UTF-8，后端日志同步修复；
  构建脚本新增源码编码检查与打包后帮助输出校验。
- 后端配套：RTT 上行环形缓存（REST 与 WebSocket 共用）、探针列表状态快照、
  connect/set-target/寄存器读取的 HTTP 超时、commander 调试锁有限等待。

## 0.1.0

- 首个版本：`backend` / `probe` / `target` / `flash` / `commander` / `debug` / `device` / `rtt` 基础命令。
