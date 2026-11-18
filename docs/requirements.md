# 运行前置条件

**结论：装完 CLI 就能用的命令占大多数；只有"碰硬件"的能力需要额外组件。**
本文按能力列出需要装什么、缺了会怎样、怎么自查。安装方式见 [README](../README.md#安装)。

## 1. 通用要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows 10/11 x64（推荐） | USB 抓包与 WinUSB 请求仅 Windows；Linux/macOS 只能用串口/网络/构建等命令 |
| Python（源码态 / wheel） | ≥ 3.9 | 单文件 `omni.exe` 自带解释器，不需要系统 Python |
| 后端进程 | `link` 与 `bus` 两个服务 | 随 GUI 安装包分发，或从 `omni-backend` 源码启动；`omni backend start` |
| 第三方 Python 包 | `kconfiglib`、`PyYAML` | wheel 会自动安装；仅工程配置 / `menuconfig` / 构建相关命令使用 |

后端默认由 CLI 按注册表自动发现；没有常驻后端时，一次性命令可加 `--managed`
临时拉起。协议细节见 [backend-protocol.md](backend-protocol.md)。

## 2. 按能力

| 能力 | 需要安装 / 配置 | 缺失时的表现 |
| --- | --- | --- |
| 探针枚举、烧录、RTT、调试 | J-Link 需装 [J-Link Software](https://www.segger.com/downloads/jlink/)（含驱动）；CMSIS-DAP 免驱 | `omni probe list` 为空；`flash` 报找不到探针 |
| 工程构建 | 对应工具链：Keil `armclang`、`arm-none-eabi-gcc`、`cmake`、`ninja` | `omni build` 报工具缺失；`omni toolchain` 打印解析来源 |
| 串口 / Modbus / 网络 | 无额外组件（Windows 10+ 自带 `usbser`，CDC 设备免驱） | — |
| **USB 描述符**（解析 / 校验 / 生成） | **无**，纯离线 | — |
| **USB CDC 收发**（M3a 已实现） | 无额外组件（Windows 10+ 自带 `usbser`） | `omni usb cdc list` 为空时先看设备管理器 |
| **USB HID 报告**（M3b 已实现） | 无额外组件（系统 HID 驱动）；设备被独占时打不开 | `omni usb hid list` 为空 / `hid_busy` |
| **USB WinUSB 请求**（M3c 已实现） | 接口需绑定 WinUSB（MS OS 2.0 描述符或 INF）；WinUSB 不支持等时传输 | `omni usb winusb list` 为空 / `winusb_not_found` |
| **USB 请求（旧：任意设备）** | 已被 WinUSB/HID/CDC 三条通道替代；任意设备需 UsbDk/自研驱动（见 `usb-raw-access.md`） | — |
| **USB 抓包**（M1 已实现） | [USBPcap](https://desowin.org/usbpcap/) 1.5.4+（官方安装器，装完重启）；**管理员终端** | `omni usb doctor` 报未安装 / 非管理员，并给出下一步 |
| **USB 测试**（M4a 已实现：离线 pcap 用例；动作步骤待 M4b） | 无额外组件（只需用例引用的 pcap 文件）；动作类步骤将来需要 WinUSB/USBPcap | `test run` 报 `pcap 不存在` 或用例格式错误 |

USB 请求不需要管理员权限，但 WinUSB 接口是**独占**的：Keil、厂商工具或其他
进程占用同一设备时，`omni usb open` 会失败并提示占用。

USBPcap 由 CLI **自动探测**（卸载注册表 → Program Files → PATH → Wireshark
extcap），不内置、不静默下载；也可以用 `--usbpcap-path` 显式指定。

## 3. 自查

```powershell
omni backend status            # link / bus 是否在运行、端口与版本
omni probe list                # 探针是否可见
omni toolchain                 # 编译器 / cmake / ninja 的解析来源
omni usb doctor                # USBPcap / 管理员 / WinUSB 环境
omni usb capture doctor        # 同上，抓包视角
```

## 4. 常见缺失

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `无法连接后端（端口无监听）` | 后端未启动 | `omni backend start bus`，或命令加 `--managed` |
| `请求超时` | 后端被硬件操作阻塞或僵死 | `omni backend stop` 后重启 |
| 设备在 `--all` 列表里但 `omni usb list` 为空 | 接口 GUID 与固件不一致，或 WinUSB 未绑定 | 检查 MS OS 2.0 描述符 / INF |
| `omni usb open` 报拒绝访问 | 设备被其他进程独占 | 关闭 Keil / 厂商工具 / 已打开的 GUI 会话 |
| `capture start` 报 `usbpcap_not_found` | 未装 USBPcap | 安装官方包并重启；或用 `--usbpcap-path` |
| `capture start` 报 `not_elevated` | 终端非管理员 | 以管理员身份重开终端 |
| 抓包有设备无数据 | 抓包晚于枚举 | 先 `capture start` 再插拔设备 |
| USB 3 设备抓不到 | USBPcap 对部分 xHCI root hub 支持有限 | 换 USB 2.0 端口 / 外接 USB 2.0 hub |

## 5. 版本基线

| 能力 | 起始版本 |
| --- | --- |
| `omni usb` 命令组（命令面） | CLI 0.28.0 / bus 0.2.0 |
| USB 抓包（M1，已实现） | CLI 0.28.x / bus 0.2.x |
| 描述符（M2，已实现） | CLI 0.29.x |
| 请求（M3a CDC / M3b HID，已实现） | CLI 0.30.x |
| 请求（M3c WinUSB，已实现） | CLI 0.31.x |
| 测试（M4a 离线用例，已实现） | CLI 0.32.x |

> 里程碑与验收标准见 `omni-backend/docs/usb-plan.md`。
