"""叶子命令共用的全局选项。

仅挂在具体的叶子 parser 上（位于子命令之后，如 `omni probe list --json`），
避免与根 parser 同名选项发生 argparse 默认值覆盖冲突。
"""

import argparse


def common_parser(include_service: bool = True) -> argparse.ArgumentParser:
    """叶子命令的共用全局选项。

    ``include_service=False`` 供 ``backend`` 子命令使用：它已经有位置参数
    ``SERVICE``，再挂 ``--service`` 会出现两个同名语义的选项。
    """

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--backend-port", type=int, default=None, metavar="PORT",
                   help="后端端口（优先于常驻/配置文件）")
    if include_service:
        p.add_argument("--service", choices=("link", "bus"), default=None, metavar="SERVICE",
                       help="后端服务（link|bus）；默认按命令组推断（serial/net/modbus/usb 走 bus，其余走 link）")
    p.add_argument("--managed", action="store_true", default=False,
                   help="临时拉起托管后端执行完关停（无需先 backend start）")
    p.add_argument("--json", dest="json_", action="store_true", default=False,
                   help="输出 JSON（供脚本/CI 消费）")
    p.add_argument("--quiet", action="store_true", default=False,
                   help="仅设置退出码，不输出任何内容")
    p.add_argument("--timeout", type=int, default=30, metavar="SEC",
                   help="后端 HTTP 超时秒数（默认 30；flash 烧录/擦除默认 180，校验/回读/填充默认 120）")
    return p


def local_parser() -> argparse.ArgumentParser:
    """离线命令的共用选项：只有输出控制，不提供后端连接参数。

    离线命令不建客户端，暴露 ``--managed`` / ``--backend-port`` / ``--timeout``
    只会让人误以为它们有效，故单独一套。
    """

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", dest="json_", action="store_true", default=False,
                   help="输出 JSON（供脚本/CI 消费）")
    p.add_argument("--quiet", action="store_true", default=False,
                   help="仅设置退出码，不输出任何内容")
    return p


def config_parser() -> argparse.ArgumentParser:
    """工程构建配置命令的共用选项（本地执行，不涉及后端）。"""

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--project-dir", default=None, metavar="PATH",
                   help="工程根目录（默认当前目录并向上查找）")
    p.add_argument("--omni-root", default=None, metavar="PATH",
                   help="omni 框架根目录（默认从工程目录向上查找）")
    p.add_argument("-b", "--board", default=None, metavar="BOARD",
                   help="板级目标名（如 stm32f407ig）")
    p.add_argument("--config-dir", default=None, metavar="DIR",
                   help="生成物目录（omni_config.h/bsp_config.h/.config/kconfig_log.txt）；"
                        "相对路径按工程目录解析；默认 boards/<board>/config")
    p.add_argument("--json", dest="json_", action="store_true", default=False,
                   help="输出 JSON（供脚本/CI 消费）")
    p.add_argument("--quiet", action="store_true", default=False,
                   help="仅设置退出码，不输出任何内容")
    return p
