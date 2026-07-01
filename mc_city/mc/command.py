"""GDMC HTTP runCommand 的健壮封装。

GDPC 的 runCommand 返回结构因版本而异，不能信第一字段。本封装只在消息里出现
明确错误关键字时才抛异常。
"""
from typing import Optional

from gdpc.interface import runCommand

from ..config import DEFAULT_HOST


class MCCommandError(RuntimeError):
    pass


_ERROR_KEYWORDS = (
    "unknown or incomplete command",
    "incorrect argument",
    "you do not have permission",
    "no permission",
    "not permitted",
    "error",
    "exception",
    "failed",
    # 中文服务端（如 1.21.11）返回本地化报错，英文关键字匹配不到会静默放过，
    # 必须一并识别，否则命令失败无人察觉。
    "错误的命令",       # incorrect argument
    "未知或不完整",     # unknown or incomplete command
    "无此命令",
)


def mc_cmd(cmd: str, host: str = DEFAULT_HOST,
           dimension: Optional[str] = None, verbose: bool = True):
    """执行一条 Minecraft 命令；遇到明确错误关键字抛 MCCommandError。"""
    cmd = cmd.strip()
    if cmd.startswith("/"):
        cmd = cmd[1:]

    results = runCommand(cmd, dimension=dimension, host=host)

    messages = []
    if isinstance(results, (list, tuple)):
        for item in results:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                messages.append("" if item[1] is None else str(item[1]))
            else:
                messages.append(str(item))
    else:
        messages = [str(results)]

    joined = " | ".join(m.replace("\n", " ") for m in messages)

    if verbose:
        print(f"[CMD] {cmd} -> {joined}", flush=True)

    lower = joined.lower()
    looks_error = any(kw in lower for kw in _ERROR_KEYWORDS)
    # gamerule usage 提示也算错
    if "usage:" in lower and "gamerule" in lower:
        looks_error = True

    if looks_error:
        raise MCCommandError(joined or f"Command failed: {cmd}")

    return results


def _try_cmd(cmd: str, host: str) -> bool:
    """跑一条命令，成功返回 True；失败打印警告返回 False（不抛，世界冻结非致命）。"""
    try:
        mc_cmd(cmd, host=host)
        return True
    except MCCommandError as e:
        print(f"[WARN] 命令失败 {cmd!r}: {e}", flush=True)
        return False


def pause_world(host: str = DEFAULT_HOST):
    """建城期间冻结世界。优先 tick freeze（1.20.2+，停全部 tick）；失败回退 gamerule。

    某些服务端（实测 1.21.11）不向命令源暴露 gamerule 子节点，gamerule 必失败，
    故 tick freeze 为主路径。两者都失败仅警告，不中断生成。
    """
    if _try_cmd("tick freeze", host):
        return
    print("[WARN] tick freeze 不可用，回退 gamerule...", flush=True)
    _try_cmd("gamerule doDaylightCycle false", host)
    _try_cmd("gamerule randomTickSpeed 0", host)


def resume_world(host: str = DEFAULT_HOST):
    """恢复世界 tick。优先 tick unfreeze；失败回退 gamerule 默认值。"""
    if _try_cmd("tick unfreeze", host):
        return
    print("[WARN] tick unfreeze 不可用，回退 gamerule...", flush=True)
    _try_cmd("gamerule doDaylightCycle true", host)
    _try_cmd("gamerule randomTickSpeed 3", host)
