"""新版本检查:默认关、版本比较、缓存 TTL、失败静默。

全部离线:``_fetch_latest`` 一律用 monkeypatch 替换,不发真实网络请求。
"""

from __future__ import annotations

import time

from foxclaw.core import update_check as uc


def _reset() -> None:
    uc._inflight = False


def test_default_disabled(monkeypatch, tmp_path):
    """不设 FOXCLAW_UPDATE_CHECK → 不查、不写缓存、不起线程。"""
    _reset()
    monkeypatch.delenv("FOXCLAW_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path))

    assert uc.status()["enabled"] is False
    uc.maybe_refresh()
    assert uc._inflight is False
    assert not (tmp_path / "update_check.json").exists()


def test_version_compare():
    assert uc.parse_version("0.1.10") == (0, 1, 10)
    assert uc.parse_version("0.2.0rc1") == (0, 2, 0)      # 预发布后缀丢弃
    assert uc.parse_version("") == ()
    assert uc.is_newer("0.1.10", "0.1.2") is True          # 字符串比较会判错,这里逐段比
    assert uc.is_newer("0.1.2", "0.1.2") is False
    assert uc.is_newer("0.1.2", "0.1.10") is False


def test_refresh_writes_cache(monkeypatch, tmp_path):
    _reset()
    monkeypatch.setenv("FOXCLAW_UPDATE_CHECK", "1")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(uc, "_fetch_latest", lambda: "9.9.9")

    uc._refresh()

    st = uc.status()
    assert st["latest"] == "9.9.9"
    assert st["update_available"] is True
    assert st["error"] is None
    assert (tmp_path / "update_check.json").exists()


def test_fresh_cache_skips_refresh(monkeypatch, tmp_path):
    """24h 内不重复查 PyPI。"""
    _reset()
    monkeypatch.setenv("FOXCLAW_UPDATE_CHECK", "1")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path))
    uc._write_cache({"latest": "9.9.9", "checked_at": time.time(), "current": "0.1.2"})

    calls: list[int] = []
    monkeypatch.setattr(uc, "_fetch_latest", lambda: calls.append(1) or "9.9.9")
    uc.maybe_refresh()
    time.sleep(0.05)

    assert calls == []
    assert uc._inflight is False


def test_failure_is_silent(monkeypatch, tmp_path):
    """网络失败不抛异常,只记 error;update_available 仍为 False。"""
    _reset()
    monkeypatch.setenv("FOXCLAW_UPDATE_CHECK", "1")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path))

    def boom():
        raise OSError("offline")

    monkeypatch.setattr(uc, "_fetch_latest", boom)
    uc._refresh()

    st = uc.status()
    assert st["update_available"] is False
    assert "offline" in (st["error"] or "")


def test_disabled_never_reports_update(monkeypatch, tmp_path):
    """缓存里就算有更新的版本,没启用也不提示(默认不外呼,也不拿旧缓存骚扰)。"""
    _reset()
    monkeypatch.delenv("FOXCLAW_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path))
    uc._write_cache({"latest": "99.0.0", "checked_at": time.time(), "current": "0.1.2"})

    st = uc.status()
    assert st["enabled"] is False
    assert st["update_available"] is False
