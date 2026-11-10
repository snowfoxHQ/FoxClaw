"""工具中心第3期 · SKILL.md 指令技能 端到端验证。"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_markdown_skills():
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    _DATA = tempfile.mkdtemp(prefix="foxclaw_md_")
    os.environ["FOXCLAW_DATA_DIR"] = _DATA

    from fastapi.testclient import TestClient
    from foxclaw.api import app

    SKILL = "---\nname: 周报助手\ndescription: 教怎么写周报\ntriggers: 周报, 汇报\n---\n先列本周成果,再列风险,最后列下周计划。"

    with TestClient(app) as c:
        print("=" * 60); print("工具中心 · SKILL.md 指令技能 验证"); print("=" * 60)

        r = c.post("/api/ahs/md-skills", json={"content": SKILL})
        assert r.status_code == 409 and r.json()["detail"]["needs_ack"] is True
        print("[1] 未确认 → 409 needs_ack OK")

        r = c.post("/api/ahs/md-skills", json={"content": SKILL, "acknowledged": True})
        assert r.status_code == 200, r.text
        sid = r.json()["skill"]["id"]
        metas = c.get("/api/ahs/md-skills").json()["skills"]
        assert any(s["id"] == sid for s in metas)
        assert all("body" not in s for s in metas)
        print("[2] 上传(ack)+列表(不含body) OK")

        r = c.get(f"/api/ahs/md-skills/{sid}")
        assert r.status_code == 200 and "成果" in r.json()["body"]
        print("[3] 查看含 body OK")

        r = c.post("/api/ahs/md-skills", json={"content": "---\ndescription: x\n---\nbody", "acknowledged": True})
        assert r.status_code == 400
        print("[4] 缺 name → 400 OK")

        assert c.patch(f"/api/ahs/md-skills/{sid}", json={"enabled": False}).json()["skill"]["enabled"] is False
        assert c.patch(f"/api/ahs/md-skills/{sid}", json={"enabled": True}).status_code == 200
        print("[5] 启停 OK")

    from foxclaw import Foxclaw
    os2 = Foxclaw(data_dir=_DATA)
    assert os2.md_skills.get(sid) is not None, "重启后指令技能丢失(持久化)"
    ag = os2.supervisor
    out = ag._skill_augment("帮我写周报", [{"role": "user", "content": "帮我写周报"}])
    assert "先列本周成果" in out[-1]["content"] and "帮我写周报" in out[-1]["content"]
    assert sum(1 for i in range(len(out) - 1) if out[i]["role"] == out[i + 1]["role"] == "user") == 0
    out2 = ag._skill_augment("订机票", [{"role": "user", "content": "订机票"}])
    assert out2[0]["content"] == "订机票"
    print("[6] 注入命中/未命中 + 持久化 round-trip OK")

    with TestClient(app) as c:
        assert c.delete(f"/api/ahs/md-skills/{sid}").status_code == 200
        assert not any(s["id"] == sid for s in c.get("/api/ahs/md-skills").json()["skills"])
    print("[7] 删除 OK")

    print("\nSKILL.md 指令技能验证全部通过")

if __name__ == "__main__":
    test_markdown_skills()
