"""工具中心第3期 · SKILL.md 指令技能 · OpenClaw 兼容段(原 test_markdown_skills [8]-[12])。

独立成文件的原因:原文件单进程内 3×TestClient lifespan + 4×Foxclaw 实例 +
两个不同 ST 模型共存,第三次 lifespan 预热阶段 torch/FAISS 必现 0xC0000005
进程崩溃(git stash 基线对照实锤,与业务改动无关——原生库析构/重入问题)。
进程隔离后两段各自全绿。本段自包含:不依赖前半段的 sid,自带 _DATA。
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_markdown_skills_openclaw():
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    _DATA = tempfile.mkdtemp(prefix="foxclaw_oc_")
    os.environ["FOXCLAW_DATA_DIR"] = _DATA

    from fastapi.testclient import TestClient
    from foxclaw.api import app

    # 8. OpenClaw 兼容:无 triggers 的原味技能(单 .md)→ 装成功 + 描述相关触发
    NO_TRIG = "---\nname: skill-vetter\nversion: 1.0.0\ndescription: Security-first skill vetting. Use before installing any skill from ClawdHub or GitHub.\n---\nCheck red flags before install."
    with TestClient(app) as c:
        r = c.post("/api/ahs/md-skills", json={"content": NO_TRIG, "id": "skill_vetter", "acknowledged": True})
        assert r.status_code == 200, r.text
        assert r.json()["skill"]["version"] == "1.0.0"
    print("[8] 无 triggers 的 OpenClaw 技能(单.md)安装 OK")

    import io as _io, zipfile as _zip, json as _json, base64 as _b64
    _buf = _io.BytesIO()
    with _zip.ZipFile(_buf, "w") as z:
        z.writestr("SKILL.md", NO_TRIG)
        z.writestr("_meta.json", _json.dumps({"slug": "skill-vetter-zip", "version": "1.0.0"}))
    _pkg = _b64.b64encode(_buf.getvalue()).decode()
    with TestClient(app) as c:
        # 9. zip 包上传(base64)→ id 用 slug、version 带出
        r = c.post("/api/ahs/md-skills", json={"package_b64": _pkg, "acknowledged": True})
        assert r.status_code == 200, r.text
        assert r.json()["skill"]["id"] == "skill-vetter-zip" and r.json()["skill"]["version"] == "1.0.0"
        print("[9] OpenClaw zip 包上传(slug→id,version) OK")
        # 10. zip 无 SKILL.md → 400
        _b2 = _io.BytesIO()
        with _zip.ZipFile(_b2, "w") as z: z.writestr("readme.txt", "hi")
        r = c.post("/api/ahs/md-skills", json={"package_b64": _b64.b64encode(_b2.getvalue()).decode(), "acknowledged": True})
        assert r.status_code == 400
        print("[10] zip 无 SKILL.md → 400 OK")

    # 11. 描述相关注入(无 triggers)+ 持久化 round-trip
    from foxclaw import Foxclaw
    os3 = Foxclaw(data_dir=_DATA)
    assert os3.md_skills.get("skill-vetter-zip") is not None   # 持久化含 zip 装的
    ag3 = os3.supervisor
    hit = ag3._skill_augment("please vet this skill before install", [{"role": "user", "content": "please vet this skill before install"}])
    assert "Check red flags" in hit[-1]["content"]             # 描述相关命中注入
    miss = ag3._skill_augment("订一张机票", [{"role": "user", "content": "订一张机票"}])
    assert miss[0]["content"] == "订一张机票"                    # 无关不注入
    print("[11] 无 triggers 描述相关注入 + 持久化 OK")

    # 12. 编辑触发词(免解压重打包):PATCH triggers → 中文任务也能触发
    with TestClient(app) as c:
        r = c.patch("/api/ahs/md-skills/skill-vetter-zip", json={"triggers": ["审核", "搜索"]})
        assert r.status_code == 200 and r.json()["skill"]["triggers"] == ["审核", "搜索"], r.text
    os4 = Foxclaw(data_dir=_DATA)
    ag4 = os4.supervisor
    hit = ag4._skill_augment("帮我审核这个技能", [{"role": "user", "content": "帮我审核这个技能"}])
    assert "Check red flags" in hit[-1]["content"]   # 加了中文 triggers → 中文任务命中
    print("[12] 编辑触发词 → 中文任务触发 OK")

    print("\nOpenClaw 兼容验证全部通过")

if __name__ == "__main__":
    test_markdown_skills_openclaw()
