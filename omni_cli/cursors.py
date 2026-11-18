"""流式读取的游标记忆（每个服务/流一份）。

CLI 的每条命令都是独立进程，无法在内存里保留「上次读到哪」。若每次都从
最旧数据开始读，``omni serial send`` 之后的 ``omni serial read`` 会把历史
重放一遍；若改成从「当前末尾」开始，又会撞上「响应比命令启动更快」的竞态
（数据已经到达缓冲区，等读却从它之后开始，于是永远等不到）。

因此把游标持久化到用户级状态目录：

- 默认**续读**：有记录就从上次的 ``cursor`` 开始，只返回新数据；
- 首次（无记录）：从最旧数据开始，便于「看看缓冲区里有什么」；
- 显式 ``--since N`` 覆盖记录（并更新它）；
- ``--since 0`` 表示明确要求从最旧开始。

存储位置：``<service 状态目录>/.read_cursor.json``（与后端登记表同目录，
不写仓库、不写 submodule）。
"""

import json
import os
import threading

from . import settings

_LOCK = threading.Lock()


def _path(service_name: str) -> str:
    return os.path.join(settings.service(service_name).state_dir(), ".read_cursor.json")


def _read_all(service_name: str) -> dict:
    try:
        with open(_path(service_name), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_cursor(key: str, service_name: str) -> int:
    """读取某条流上次的游标；没有记录返回 0（= 从最旧数据开始）。"""
    with _LOCK:
        try:
            return max(0, int(_read_all(service_name).get(key) or 0))
        except (TypeError, ValueError):
            return 0


def save_cursor(key: str, cursor: int, service_name: str) -> None:
    """记录游标；失败静默（只是优化体验，不影响命令结果）。"""
    if not key or cursor is None:
        return
    with _LOCK:
        data = _read_all(service_name)
        data[key] = max(0, int(cursor))
        path = _path(service_name)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError:
            pass


def clear_cursor(key: str, service_name: str) -> None:
    with _LOCK:
        data = _read_all(service_name)
        if data.pop(key, None) is None:
            return
        try:
            with open(_path(service_name), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
