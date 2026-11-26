# 版本记录

> `omni` CLI 的完整版本历史（从 0.1.0 起），从 README 迁出。
> 安装、命令参考与测试见 [README](../README.md)。

## 0.51.0（2026-11-26）

- `usb desc parse` 的人读输出补齐 BOS 与 MS OS 2.0：BOS 打印总长与能力列表，平台能力解出
  UUID；MS OS 2.0 平台能力解出 Windows 版本、描述符集长度、vendor code 与 alt enum。
  `msos20` 文档渲染配置子集、函数子集、兼容 ID（如 `WINNCM`）、注册表属性与未识别子描述符。
- `probe connect` 遇到 `J-Link is already open` 时直接给出排查顺序：删
  `%TEMP%\.pylink-usb-<serial>.lck` → 查占用进程（nrfutil-device / JLink.exe / Keil / Ozone）
  → 拔插探针 → `omni backend stop link && omni backend start link`。
- `usb doctor` 与 `usb capture` 的非管理员提示改为「先直接抓，USBPcapCMD 报 access denied
  再提权」：USBPcap 1.5.4 起普通用户即可抓包，`not_elevated` 文案改为「USBPcapCMD 拒绝访问」。

## 0.50.0（2026-11-24）

- `omni usb list` 使用 bus 后端 0.3.0+ 的 Windows hub IOCTL 地址，不启动 USBPcap 也能
  显示当前 `bus.device`；表头改为「USB 地址」。后端查询失败或版本较旧时，仍回退到
  0.49.0 的实时抓包描述符映射。
- 动态地址按 PnP driver key 精确关联，复合设备的父设备和各接口共享同一地址；重新插拔、
  USB 复位或重新枚举后再次执行 `usb list` 即读取新地址，不缓存旧值。

## 0.49.0（2026-11-24）

- `omni usb list` 新增「抓包地址」列，把正在进行的 USBPcap 实时抓包描述符映射为
  `bus.device`（如 `2.26`）；`--all` 树形视图同步显示，`--json` 每个设备新增
  `captureAddresses` 数组。映射同时核对所属 `USBPcapN` 与 VID/PID，同一 hub 上存在
  多个相同 VID/PID 时保留全部候选，有 USB 序列号时进一步消除歧义。
- 未运行实时抓包、抓包晚于设备枚举或后端不支持抓包查询时，抓包地址显示 `-`，不会把
  Windows 物理端口号或离线 pcap 中的旧地址误当成当前动态地址。

## 0.48.0（2026-11-24）

- `-t footprint` 一次给出 ROM 与 RAM 两份符号级报告；也可以逗号组合
  （`-t rom-report,ram-report`）。`--json` 的 `footprint` 变成按种类索引的对象
  （`{"rom": {...}, "ram": {...}}`）。
- `report_kinds` 对拼错的目标整体退化为 cmake 目标透传，不会静默吞掉。

## 0.47.0（2026-11-24）

- 新增 `omni build -t rom-report` / `-t ram-report`：构建成功后按**符号**打印 ROM / RAM 占用
  （前 20 个符号 + 其余合计 + 未归入符号的栈/堆/对齐），`--json` 里对应 `footprint` 字段。
  实现直接解析 ELF 的符号表，AC6 的 `omni.axf` 与 GCC/CLANG 的 `omni.elf` 同一套代码，
  不依赖 `nm`/`fromelf`，也不需要链接器 map 里的分目标尺寸表。
- `-t` 的其它值仍按原样透传给 `cmake --build --target`（如 `-t <自定义 ninja 目标>`）。

## 0.46.0（2026-11-24）

- `config generate` 额外输出 `<config-dir>/kconfig_sources.txt`：本次**实际解析到的**
  Kconfig 文件清单（每行一个绝对路径）。构建系统把它挂到 `CMAKE_CONFIGURE_DEPENDS` 后，
  改任意一个 Kconfig（含新增驱动目录里的）都会自动重配，不再需要手工 `omni build --cmake`。
  做法对齐 Zephyr 的 `PARSED_KCONFIG_SOURCES_TXT`（`zephyr/cmake/modules/kconfig.cmake`）。
- `config --json` 的 `generated` 列表包含这个文件。

## 0.45.1（2026-11-24）

- **恢复 `omni build` 结尾的段级细账**：0.45.0 把 `size  Code=… RO-data=… RW-data=…
  ZI-data=…`（AC6/AC5）和 `size  text=… data=… bss=…`（GCC/CLANG）这行改成了只在
  `-v` 时打印，默认输出只剩区域进度条。现在两者都打印：先区域条，再段级细账，信息与
  0.44 及更早版本一致。
- 区域进度条本身不变（数据来源、`--json` 的 `memory_regions`、`-t size` 修复都不变）。

## 0.45.0（2026-11-24）

- **`omni build` 结束现在打印内存区域占用（ASCII 进度条）**：链接完成后按链接脚本里的
  区域（`FLASH` / `RAM` / `CCMRAM` …）给出「已用 / 上限 / 百分比」，像 west 的内存报告
  但排版固定、不随链接器变：

  ```text
  构建完成  18 步  3.0s
    FLASH   [#...................]  17672 B /   1 MB   1.69%
    RAM     [#...................]   4544 B / 128 KB   3.47%
    CCMRAM  [....................]      0 B /  64 KB   0.00%
  ```

  数据来源：GCC/CLANG 取链接器 `-Wl,--print-memory-usage` 的已用字节与链接 map
  `Memory Configuration` 的区域上限；AC6/AC5 取 armlink map 的 `Load Region` /
  `Execution Region`。增量构建没有重新链接时按 map 里段的 VMA/LMA 求和（ELF 里
  `SHT_NOBITS` 的 `.bss` 不算 Flash）；lld 的 map 不含区域上限，复用上一次链接缓存到
  `<build-dir>/omni.memory.json` 的结果。链接器原来那段每行夹空行的
  `Memory region ...` 原始输出不再逐行转发，原文仍完整保留在 `build.log`。
- 段级细账（`Code=/RO-data=...` 或 `text=/data=/bss=`）当时只在 `-v` 时打印
  （0.45.1 起恢复为默认打印）。没有链接 map（例如只配置了没链接）时不显示进度条，仍打印细账。
- `--json` 新增 `memory_regions`：`[{"name", "used", "capacity", "percent"}]`，脚本/CI
  可以直接拿百分比做门禁（`size` / `gnu_size` 字段保持不变）。
- 修复 **`omni build -t size`**：size 目标名改为 configure 之后从 `build.ninja` 解析
  （真实名字是 `omni-size-<CMake 可执行目标>`），不再假设它等于工程目录名。此前
  `project(template)` + `add_executable(app)` 这类工程会报
  `ninja: error: unknown target 'omni-size-template'`；工程里存在多个 size 目标且都
  对不上工程名时会报错并列出候选，用 `-t <cmake target>` 明确指定。

## 0.45.0（2026-11-24）

- `config generate` 额外输出 `<config-dir>/kconfig_sources.txt`：本次**实际解析到的**
  Kconfig 文件清单（每行一个绝对路径）。构建系统用它挂 `CMAKE_CONFIGURE_DEPENDS`，
  改任意一个 Kconfig（含新增驱动目录里的）都会自动重配，不再需要手工 `omni build --cmake`。
  做法对齐 Zephyr 的 `PARSED_KCONFIG_SOURCES_TXT`（`zephyr/cmake/modules/kconfig.cmake`）。
- 顺带：`config --json` 的 `generated` 列表里包含这个文件。

## 0.44.0（2026-11-24）

- **menuconfig 改动会持久化**：`omni config menuconfig` 保存后，把它相对"生成前配置"的实际
  改动写进 `boards/<board>/board.conf` 末尾的受管块（`# >>> omni menuconfig ... # <<<`），
  随后按 `prj.conf` + `board.conf` 重新生成头文件。下一次 `omni config generate` 不会再丢掉
  交互式改动。只写有 prompt 的符号（派生符号写进片段会让下次 configure 报错）；
  `--no-save` 保留旧行为（只更新头文件，不回写）。
- 新增 `--config-dir DIR`（`config` / `config generate` / `config menuconfig` /
  `config bsp` / `config sync-keil` 通用）：把**全部生成物**写到一个目录——`omni_config.h`、
  `bsp_config.h`、`.config`、`kconfig_log.txt`。相对路径按工程目录解析；缺省仍是
  `boards/<board>/config`（cbuild / Keil 依赖该路径，保持兼容）。
- `.config` 与 `kconfig_log.txt` 从工程根移入生成物目录：工程根不再因为一次 configure
  多出两个文件。`omni config sync-keil` 会依次查找 `--config-dir`、`boards/<board>/config`、
  工程根，旧布局仍可用。
- 合成 Kconfig 的临时文件改写进生成物目录，且 `source` 用**绝对路径**：omni 作为只读
  子模块（打包/容器）时也能配置，不再往 `omni/` 根写临时文件。
- `config bsp --map` 标记为废弃并打印警告：`bsp_config.h` 只从源码扫描生成，链接 map
  不再参与（0.43.0 起已如此，但参数此前被静默忽略）。
- `config --json` 增加 `config_dir` 字段，便于脚本定位生成物。

## 0.43.0（2026-11-23）

- `config bsp` / `config generate` 生成的 `bsp_config.h` 现在同时声明**板级导出对象**：
  板目录下非 `static` 的 `const <type>_spec_t <name> = ...`（如 `const gpio_spec_t led`）
  生成 `extern const struct gpio_spec led;`。板级对象从此有编译期契约，应用不必再为
  `led` / `key` 之类手写 `extern`；`static` 对象仍跳过（板内私有，如 `led0_spec`）。
- 声明用结构体标签形式（`struct gpio_spec` 而不是 `gpio_spec_t`）：这个头由
  `omni/config.h` 全局包含，可能早于驱动头，标签形式不依赖 typedef 与包含顺序。
- 头文件 banner 改为说明真实来源（`omni config` 从板级源码生成），不再指向已迁走的
  `omni/scripts/omni_bspgen.py`。
- `bsp_config.h` 固定以 LF 写盘（与 `uvproj_sync` / `modules` 的生成物一致）：此前在 Windows
  上写 CRLF，与仓库里的 LF blob 不符，每次生成都会整文件改动。

## 0.42.0（2026-11-20）

- `config generate --msp` 标记为废弃：生成的 `driver_msp_config.h` 在本仓无任何驱动 include，
  也没有任何 SoC 提供模板文件。引脚/时钟/DMA 的实际归属是 vendor（CubeMX/SoC 的 `GPIO_Config()`），
  所以该选项不再有意义。**行为暂不变**（仍会生成），但会打印废弃警告，后续版本移除。
- `omni build` 的链接 map 名跟镜像名走：GCC/CLANG 由 `omni.axf.map` 改成 `omni.elf.map`
  （AC6 仍是 `omni.axf.map`），与 cbuild 的 `<image>.map` 一致；产物汇总与 `--json` 的
  `artifacts` 里出现的就是对应镜像的那个名字。

## 0.41.0（2026-11-19）

- `flash program --erase {sector,chip}`：可选擦除模式。默认 `sector` 只擦镜像覆盖的扇区；
  `chip` 整片擦除（本机 256 KB 实测 0.3 s，比逐扇区 18.9 s 快得多，但随后要重写全部页）。
- `flash program` 返回阶段耗时：`--json` 增加 `erase_ms` / `program_ms` / `verify_ms`，
  人类输出把它们附在总耗时后面。实测 105 KB：sector 擦除 18.9 s、校验 18.5 s；
  chip 擦除 0.3 s + 编程 25.1 s。
- `usb capture devices` 树补 IAD 与字符串：新增 `IAD 接口关联` 节点（接口范围 / 功能类 /
  `iFunction` 字符串），配置节点带配置字符串，接口节点带接口名（需要抓包里存在对应
  STRING 描述符）。
- `debug mem read` 默认改为阻塞读（等价 `--block`）；新增 `--no-block` 保留"探针被占用
  就跳过"的旧语义（GUI 周期刷新用）。
- `backend start` 在握手文件之后再确认一次存活：进程已退出或端口 3 s 内不可达时报错
  （附后端日志尾部），不再把刚死掉的后端报成"已启动"。

## 0.40.0（2026-11-19）

- `commander exec` 不再崩溃：`commander exec <cmd...>` 把参数解析成 list，服务路由把它当
  dict key 用，触发 `TypeError: unhashable type: 'list'`。
- `debug mem read` 不再静默返回空数据：非侵入读取用 try-acquire 拿探针锁，RTT 抓取运行中
  会被跳过（实测 6 次跳过 3 次），旧版仍报 `success: true` 且退出码 0。现在 JSON 增加
  `warning` 字段并以退出码 1 结束，人类可读模式提示"探针被占用（如 RTT 抓取）"。
- `debug mem read --block`：阻塞等探针空闲再读（halt + 读），RTT 抓取开着也能稳定取到值
  （实测 6/6 成功）；后端 `/zone/memory/read` 新增 `block` 字段区分"显式读取"与 GUI 刷新。
- `probe list|status --no-sectors`：省略 `target.sectors` 全量表，256 KB Flash 的
  `probe status --json` 从 25 KB 降到 0.75 KB（保留 `sector_count` 与页/扇区大小）。
- `usb desc parse` 语义化：IAD 显示为"接口关联"并带 `iFunction`/`iInterface`；CDC 功能
  描述符显示 Header/Call-Management/ACM/Union/NCM/…；接口行补类名（如 `CDC-Data(0x0A)`）。
- `debug watch eval` 在 `info` 为空或 `available=false` 时报错（JSON 带 `warning`，退出码 1），
  后端重启丢 ELF 会话时提示先 `debug elf load`；不再 `success: true` 返回空结果。
- `flash program` 失败且错误含 "not connected" 时提示先 `probe disconnect` + `connect`
  （探针句柄被 JLink.exe / Keil 占用过后会出现）。
- `net ping` 全丢包时提示目标可能被 halt，或本机网卡/静态 IP 未配置。

## 0.39.1（2026-11-19）

- 「OMNI CLI Shell」横幅显示后端的提权状态：`后端 bus: 运行中 …（后端未提权：capture start 会失败）`。
  管理员终端只代表客户端，抓包权限看的是已经在跑的后端进程——先 `backend stop` 再由管理员
  终端 `backend start` 才能提权。

## 0.39.0（2026-11-19）

- 复合设备（`0xEF/0x02`）的控制请求按接口类补全解码：RNDIS（`0xEF/0x04`）的
  `SEND_ENCAPSULATED_COMMAND` / `GET_ENCAPSULATED_RESPONSE` 不再显示成 `CLASS_0x00`；
  CDC/HID/MSC/Hub 的接口同样受益。

## 0.38.0（2026-11-19）

- USBD 状态码补全：`CRC`、`XACT_ERROR`、`BABBLE_DETECTED`、`ENDPOINT_HALTED`、
  `DEVICE_GONE`、`INVALID_PARAMETER`、`CANCELED`、`ERROR_SHORT_TRANSFER` 等；设备拔插/复位后
  不再显示 `USBD_0xXXXXXXXX`。

## 0.37.0（2026-11-19）

- URB 行的 Function 名补齐（`ABORT_PIPE`、`RESET_PIPE`、`SYNC_RESET_PIPE_AND_CLEAR_STALL`、
  `GET_MS_FEATURE_DESCRIPTOR` 等），不再只显示 `URB_0xNNNN`；其中 `0x002A` 是 Windows
  枚举时查询 MS OS 2.0 描述符。
- Hub class 请求按端口特性解码：`SET_FEATURE(PORT_RESET, port N)`、
  `CLEAR_FEATURE(PORT_SUSPEND, port N)` 等。suspend/resume 本身不是 URB，抓不到；
  reset/suspend 的意图可以从 hub 请求和管道控制 URB 看出来。

## 0.36.0（2026-11-19）

- `usb list --all` 改成带连接线的树：`USB → USBPcapN（root hub）→ 设备`，用
  `├──` / `└──` / `│` 表达层级（`--json` 结构不变）；AGENTS 里补了这条树形数据视图的例外。

## 0.35.0（2026-11-19）

- `usb capture transfers` 改成 Bus Hound 风格：用 **Phase** 列表示阶段，控制传输拆成
  `CTL`（SETUP 八字节）和 `IN`/`OUT`（数据阶段）两行；`--no-setup` 可只保留数据阶段。
- 列顺序调整为 `ID 时间 设备 端点 Phase 长度 状态 数据 描述`：去掉「方向」「类型」，
  「摘要」换成「描述」——控制传输给请求名，批量/中断只保留 MSC/CDC 类级解码，
  `BULK IN 18B` 这类噪音不再显示。

## 0.34.0（2026-11-19）

- `omni usb list` 新增「所属 hub」列（USBPcapN）与 `--all` 树形视图：按 root hub 分组，
  显示控制器名/PCI ID、`\\.\USBPcapN` 与当前绑定标记，bind 前可直接确认设备在哪颗 hub。
- 映射来源：USBPcap 服务的实例顺序（`HKLM\SYSTEM\CurrentControlSet\Services\USBPcap\Enum`）
  对应 `USBPcapN`，设备归属用 CfgMgr32 父链上溯到 `USB\ROOT_HUB`（后端新增
  `core/usb_topology.py`）。
- `usb list` 默认表格去掉「实例 ID」列（`--json` 仍保留），避免加 hub 列后过宽。

## 0.33.2（2026-11-19）

- `transfers --follow` 带 `--vid/--pid` 时，先检查当前抓包里有没有该设备的描述符；没有就
  立即提示「过滤不会命中、先 stop/start 再插拔；仍没有则换 USBPcap2」，不再静默等待。

## 0.33.1（2026-11-19）

- `transfers --vid/--pid` 没有命中时区分两种情况：完全没抓到数据 vs 抓到了但映射不到该
  VID/PID（设备在 capture start 前已枚举），后者给出「重启抓包再插拔」的下一步；
  `--follow` 的等待提示也带上过滤条件。

## 0.33.0（2026-11-19）

- 收敛 `omni usb` 命令面：
  - 删除未实现的 `usb capture replay`（help 里像能用，实际只返回里程碑提示）；
  - `usb capture doctor` 与 `usb doctor` 是同一处理器，合并为 `usb doctor`；
  - 文档/skill 同步：设备入口是 `usb list`，环境诊断是 `usb doctor`，抓包生命周期是
    `capture bind → start → transfers/detail → stop`。

## 0.32.0（2026-11-19）

- 新增 `usb capture bind` / `usb capture unbind`：绑定默认 root hub（写入用户级
  `cli.config.json`），之后 `capture start` 不用再传 `--device`。优先级：显式 `--device` >
  绑定 > 环境中唯一的一个；多个又没绑定时报错并列出可选值。
- `usb capture doctor` 显示当前绑定的 hub。

## 0.31.1（2026-11-19）

- `usb capture transfers` 的 `--device` 只接受设备地址；传 root hub 路径（`\\.\USBPcap1`）时
  直接提示「这里是设备地址、root hub 在 capture start 里选」，不再抛 argparse 的 invalid int。
- `usb capture transfers --endpoint` 支持 `0x81` 十六进制写法。

## 0.31.0（2026-11-19）

- `usb capture transfers` 新增 `--vid` / `--pid`：按抓包里的设备描述符映射到设备地址后过滤视图
  （要求抓包期间抓到该设备的 GET_DESCRIPTOR）；后端 `/api/usb/transfers` 同步支持
  `vid` / `pid` 参数。

## 0.30.1（2026-11-19）

- 修复子命令帮助错行：argparse 计算帮助列时少算 2 列缩进，最长命令名（如 `transfers`）
  被挤到下一行；现在所有子命令组（`usb capture`、`usb winusb`、`serial`、`net` 等）都对齐。

## 0.30.0（2026-11-19）

- 新增 `omni usb list`：列出系统 USB 设备（VID/PID、描述、驱动服务、实例 ID），支持
  `--vid` / `--pid` / `--driver` 过滤与 `--json`；`usb winusb list --all` 保留为等价入口。

## 0.29.2（2026-11-19）

- `usb capture transfers` 的控制传输在数据列首行显示 SETUP 八字节（`S:` 前缀，如
  `S:80 06 00 01 00 00 12 00`），`--no-setup` 可关；需要后端列表接口返回 `setupHex`
  （源码后端重启即可，旧后端只是不显示这一行）。

## 0.29.1（2026-11-19）

- 数据列默认 8 字节/行在 120 列控制台也能放下（摘要列下限从 24 收到 16），不再退化成 4 字节。
- 「OMNI CLI Shell」优先在 Windows Terminal 中打开（`wt.exe`，滚动历史不会被打断），
  没装或加 `-NoTerminal` 时退回当前控制台。

## 0.29.0（2026-11-19）

- `omni usb capture start --device USBPcap1` 这类短名会补全成 `\\.\USBPcap1`：USBPcapCMD
  的 `-d` 只认设备路径，传短名会立即退出且不报错，表现为「已开始捕获」但一条传输都没有。
- `omni usb capture doctor` 的「可抓 root hub」改为打印设备路径，可直接复制给 `--device`。
- `omni usb capture transfers` 新增「数据」列（排在摘要之前，和 Bus Hound 一致）：payload 以
  hex 按行显示，默认每行 8 字节，`--data-per-line 4|8|16` 可调，窗口过窄时自动降到 4，
  超长部分用 `…` 标注；`--data-bytes N` 控制显示字节数（默认 64，0 表示隐藏该列）。
- 表格渲染按终端显示宽度对齐（CJK 记 2 列），支持单元格内换行；整表超宽时先压摘要列，
  中文表头不再错位，数据列不会因窗口折行而跑到行首。
- 已安装 GUI 的后端查找不再依赖 C 盘默认路径：CLI 从注册表 `Uninstall` 键的
  `InstallLocation` / `DisplayIcon` / `UninstallString` 反推安装根目录，任意盘符、
  per-user 与 per-machine 两种安装都能复用；`backend start` 失败提示改为列出实际
  检查过的目录，并点名 `OMNI_LINK_HOME` / `OMNI_BUS_HOME`（原先误指 `OMNI_*_STATE_DIR`）。
- 新增 `scripts/dev-bus.ps1`：一键停掉现有后端并改用源码后端。
- 安装包新增「OMNI CLI Shell」开始菜单入口（`scripts/omni-shell.ps1`）：打开就是一个已配置好
  `omni` 的终端（类似 ESP-IDF Command Prompt），自动识别安装版 / 源码版；USB 抓包右键选
  「以管理员身份运行」。
- `scripts/build-exe.ps1` 补 UTF-8 BOM：Windows PowerShell 5.1 下无 BOM 会把中文字符串按 GBK
  解码，脚本直接解析失败。

## 0.28.1（2026-11-18）

- CLI 呈现统一到 `python -h` / `pip -h` 的风格：帮助只做缩进对齐与按显示宽度折行，
  不加颜色和符号装饰；分组标题改用英文（`Backend` / `Debug & Flash` / `Project Build` /
  `Serial / Network / USB`），栏目名为 `commands:`，示例块统一为 `Examples:`。
- 示例块改用 `RawDescriptionHelpFormatter` 渲染，保持逐行排版（此前会被 argparse 压成一段）。
- 帮助文案统一：`缺省` 改 `默认`、`枚举` 改 `列出`、去掉与 `--timeout` 重复的逐命令超时说明、
  统一「查询状态」「开启/关闭」等句式。
- `omni build` 输出去掉 `›` / `✓` / `✗` / `·` 等装饰，结论行改为纯文本（着色仍由 `--color` 控制）。
- `omni usb capture open` 的解析进度只在终端原地刷新，管道/重定向下不输出。
- `build.ps1` 默认一次构建三种产物（exe + wheel/portable zip + Setup.exe）；
  只想出 exe 用 `-Exe`，跳过安装包用 `-NoInstaller`，跳过发布包用 `-NoRelease`。

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
