"""可选的新版本检查(默认关)。

为什么默认关:foxclaw 的定位是本地优先 —— 默认外呼会让「断网也能用」多一个
例外,也给安全审计多一个出网点。**只有** 设置 ``FOXCLAW_UPDATE_CHECK=1`` 时才会
访问 PyPI,其余情况 ``status()`` 里 ``enabled=False``、``update_available=False``。

失败一律静默:查版本是锦上添花,不能因为它让 ``/api/system/info`` 变慢或报错。
- 结果按 24h 缓存到 ``<FOXCLAW_DATA_DIR>/update_check.json``;
- 查询在 daemon 线程里跑,不阻塞请求;
- 查失败时保留上次成功的结果(只是不刷新时间),避免一次网络抖动让提示消失。

只发一个 GET ``https://pypi.org/pypi/foxclaw/json``,不带任何本机信息。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_PYPI_URL = "https://pypi.org/pypi/foxclaw/json"
_TTL = 24 * 3600
_TIMEOUT = 3.0

_lock = threading.Lock()
_inflight = False
_last_error: str | None = None


def _enabled() -> bool:
    return os.environ.get("FOXCLAW_UPDATE_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}


def _current() -> str:
    # 延迟导入:``foxclaw/__init__`` 在包加载期就会 import core 下的模块,模块级取
    # ``__version__`` 会形成循环导入。
    from foxclaw import __version__

    return __version__


def _data_dir() -> Path:
    return Path(os.environ.get("FOXCLAW_DATA_DIR") or "./foxclaw_data").expanduser()


def _cache_path() -> Path:
    return _data_dir() / "update_check.json"


def parse_version(value: str) -> tuple[int, ...]:
    """``"0.1.10"`` → ``(0, 1, 10)``。只取各段的前导数字,预发布/本地后缀丢掉。

    不引入 ``packaging``:这里只需要「谁更新」,自己比数字段足够,也少一个依赖。
    """
    parts: list[int] = []
    for chunk in str(value or "").strip().split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """latest 是否严格新于 current(逐段比,缺的段补 0)。"""
    left, right = parse_version(latest), parse_version(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _read_cache() -> dict[str, Any]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(data: dict[str, Any]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _fetch_latest() -> str:
    import urllib.request

    req = urllib.request.Request(
        _PYPI_URL,
        headers={"Accept": "application/json", "User-Agent": f"foxclaw/{_current()}"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 - 地址是常量 https
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    version = ((payload or {}).get("info") or {}).get("version") or ""
    if not version:
        raise ValueError("PyPI 响应里没有 info.version")
    return str(version)


def _refresh() -> None:
    global _inflight, _last_error
    try:
        latest = _fetch_latest()
        _write_cache({"latest": latest, "checked_at": time.time(), "current": _current()})
        _last_error = None
    except Exception as exc:      # 网络/解析失败都不该冒泡到请求路径
        _last_error = f"{type(exc).__name__}: {exc}"
    finally:
        with _lock:
            _inflight = False


def maybe_refresh() -> None:
    """启用且缓存过期 → 后台查一次。幂等、非阻塞,可放心在每个 system/info 里调。"""
    global _inflight
    if not _enabled():
        return
    checked = _read_cache().get("checked_at")
    if isinstance(checked, (int, float)) and time.time() - checked < _TTL:
        return
    with _lock:
        if _inflight:
            return
        _inflight = True
    threading.Thread(target=_refresh, name="foxclaw-update-check", daemon=True).start()


def status() -> dict[str, Any]:
    """给 ``/api/system/info`` 的只读快照。未启用时 latest 仍是缓存值,但不报可更新。"""
    enabled = _enabled()
    current = _current()
    cache = _read_cache()
    latest = str(cache.get("latest") or "")
    return {
        "enabled": enabled,
        "current": current,
        "latest": latest or None,
        "update_available": bool(enabled and latest and is_newer(latest, current)),
        "checked_at": cache.get("checked_at"),
        "error": _last_error,
    }
