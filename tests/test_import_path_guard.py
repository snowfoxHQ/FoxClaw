"""导入路径准入 + 技能副作用默认值的回归。

两条都是「默认配置不安全」类问题,不是功能 bug:
- ``csv_path`` 原样进 ``open()`` → HTTP 面等于开放「读本机任意文件」;
- ``SkillSpec.side_effect`` 默认 ``read`` → 漏声明的技能等于放行(与工具侧 fail-closed 相反)。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── resolve_import_path ────────────────────────────────────────────

def _csv(path: Path, text: str = "amount\n-1\n") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_inside_data_dir_ok(tmp_path, monkeypatch):
    from foxclaw.core.paths import resolve_import_path

    root = tmp_path / "data"
    root.mkdir()
    f = _csv(root / "a.csv")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(root))

    assert resolve_import_path(str(f)) == f.resolve()


def test_outside_root_rejected(tmp_path, monkeypatch):
    from foxclaw.core.paths import resolve_import_path

    root = tmp_path / "data"
    root.mkdir()
    secret = _csv(tmp_path / "secret.csv")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(root))
    monkeypatch.delenv("FOXCLAW_IMPORT_ROOTS", raising=False)

    with pytest.raises(ValueError, match="不在允许的导入目录内"):
        resolve_import_path(str(secret))


def test_sibling_prefix_not_allowed(tmp_path, monkeypatch):
    """`data2/` 不能被 `data/` 的根放行(前缀比较会误判,必须按路径段比)。"""
    from foxclaw.core.paths import resolve_import_path

    root = tmp_path / "data"
    root.mkdir()
    sibling = tmp_path / "data2"
    sibling.mkdir()
    f = _csv(sibling / "a.csv")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(root))

    with pytest.raises(ValueError):
        resolve_import_path(str(f))


def test_import_roots_env_opens_extra_dir(tmp_path, monkeypatch):
    from foxclaw.core.paths import resolve_import_path

    root = tmp_path / "data"
    root.mkdir()
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    f = _csv(downloads / "bank.csv")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(root))
    monkeypatch.setenv("FOXCLAW_IMPORT_ROOTS", f"{downloads}{__import__('os').pathsep}{tmp_path / 'other'}")

    assert resolve_import_path(str(f)) == f.resolve()


def test_suffix_whitelist(tmp_path, monkeypatch):
    from foxclaw.core.paths import resolve_import_path

    root = tmp_path / "data"
    root.mkdir()
    f = root / "env.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(root))

    with pytest.raises(ValueError, match="不支持的扩展名"):
        resolve_import_path(str(f))


def test_missing_file_rejected(tmp_path, monkeypatch):
    from foxclaw.core.paths import resolve_import_path

    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="不存在"):
        resolve_import_path(str(tmp_path / "nope.csv"))


# ── HTTP 端点 ──────────────────────────────────────────────────────

def test_endpoint_rejects_outside_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HKC_EMBEDDING", "stub")
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(root))
    monkeypatch.delenv("FOXCLAW_IMPORT_ROOTS", raising=False)
    secret = _csv(tmp_path / "secret.csv")

    from fastapi.testclient import TestClient
    from foxclaw.api import app

    with TestClient(app) as c:
        r = c.post("/api/finance/import", json={"csv_path": str(secret)})
        assert r.status_code == 400, r.text
        assert "csv_path 不允许" in r.text


def test_endpoint_accepts_inside_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HKC_EMBEDDING", "stub")
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(root))
    f = _csv(root / "ok.csv", "amount,category\n-12,food\n-3,transport\n")

    from fastapi.testclient import TestClient
    from foxclaw.api import app

    with TestClient(app) as c:
        r = c.post("/api/finance/import", json={"csv_path": str(f)})
        assert r.status_code == 200, r.text
        assert r.json()["imported"] == 2


# ── 技能副作用默认值 ───────────────────────────────────────────────

def test_skill_default_side_effect_is_write():
    from foxclaw.skills import SkillSpec

    spec = SkillSpec(id="undeclared", name="未声明", description="")
    assert spec.side_effect == "write"


def test_undeclared_skill_asks_for_confirmation():
    """鸭子类型对象没有 side_effect 属性时,也必须落到「需确认」而不是只读放行。"""
    from types import SimpleNamespace

    from foxclaw.skills.permission import evaluate

    skill = SimpleNamespace(id="duck", kind="local", external=False,
                            trust_level=lambda: "high")
    decision = evaluate(skill, {})
    assert decision.decision == "ask", decision
    assert decision.stage == 4


def test_declared_read_skill_still_allowed():
    """显式声明 read 的技能不受影响 —— 默认值只惩罚「漏写」。"""
    from types import SimpleNamespace

    from foxclaw.skills.permission import evaluate

    skill = SimpleNamespace(id="reader", kind="local", side_effect="read",
                            external=False, trust_level=lambda: "high")
    assert evaluate(skill, {}).decision == "allow"
