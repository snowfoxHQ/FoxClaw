"""工具中心第1期 · 技能台真实化 + 安装审核门 + 治理 端到端验证。
判定:assert 失败→Traceback→run_all 记失败;正常退出=通过。"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_tool_center():
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    _DATA = tempfile.mkdtemp(prefix="foxclaw_toolc_")
    os.environ["FOXCLAW_DATA_DIR"] = _DATA

    from fastapi.testclient import TestClient
    from foxclaw.api import app

    WEBAPI = {"id": "wx_weather", "name": "天气", "description": "查天气", "kind": "web_api",
              "triggers": ["天气"], "side_effect": "read",
              "endpoint": "https://example.com/api/weather", "http_method": "GET"}

    with TestClient(app) as c:
        print("=" * 60); print("工具中心 · 技能台 + 审核门 + 治理 验证"); print("=" * 60)

        r = c.post("/api/ahs/skills", json=WEBAPI)
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True
        print("[1] 安装 web_api 技能 OK")

        r = c.get("/api/ahs/skills"); assert r.status_code == 200
        ids = {s["id"]: s for s in r.json()["skills"]}
        assert "wx_weather" in ids and ids["wx_weather"]["source"] == "user"
        print("[2] 列表含新技能且 source=user OK")

        r = c.post("/api/ahs/skills", json={**WEBAPI, "id": "x_local", "kind": "local"})
        assert r.status_code == 400
        print("[3] 拒绝 local kind(400) OK")

        r = c.post("/api/ahs/skills", json={**WEBAPI, "id": "x_bad", "endpoint": "ftp://x"})
        assert r.status_code == 400
        print("[4] 拒非法 endpoint(400) OK")

        r = c.post("/api/ahs/skills", json={"id": "x_mcp", "name": "m", "kind": "mcp", "side_effect": "read"})
        assert r.status_code == 400
        print("[5] mcp 缺 server/tool(400) OK")

        r = c.patch("/api/ahs/skills/wx_weather", json={"enabled": False})
        assert r.status_code == 200 and r.json()["skill"]["enabled"] is False
        r = c.patch("/api/ahs/skills/wx_weather", json={"enabled": True})
        assert r.json()["skill"]["enabled"] is True
        print("[6] 启停切换 OK")

        r = c.get("/api/ahs/skills")
        builtin = next((s["id"] for s in r.json()["skills"] if s["source"] == "builtin"), None)
        if builtin:
            rd = c.delete(f"/api/ahs/skills/{builtin}")
            assert rd.status_code == 409, rd.text
            print("[7] 内置技能不可删(409) OK")
            rc = c.post("/api/ahs/skills", json={**WEBAPI, "id": builtin})
            assert rc.status_code == 409, rc.text
            print("[7b] 用内置 id 安装被拒(409) OK")
        else:
            print("[7] 跳过(无内置技能)")

        r = c.post("/api/ahs/skills/wx_weather/test", json={"args": {}})
        assert r.status_code == 200 and "success" in r.json()
        print("[8] 测试调用返回 SkillResult OK")

        r = c.post("/api/ahs/skills", json={"id": "ssrf_t", "name": "s", "description": "", "kind": "web_api",
                   "triggers": ["x"], "side_effect": "read", "endpoint": "http://127.0.0.1:8000/api/status"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error"] == "install_denied"
        print("[9] 安装审核门 deny 本地地址(403) OK")

        _send = {"id": "send_x", "name": "发信", "description": "", "kind": "web_api",
                 "triggers": ["发"], "side_effect": "send", "endpoint": "https://example.com/send"}
        r = c.post("/api/ahs/skills", json=_send)
        assert r.status_code == 409 and r.json()["detail"]["needs_ack"] is True, r.text
        r = c.post("/api/ahs/skills", json={**_send, "acknowledged": True})
        assert r.status_code == 200, r.text
        print("[9b] 副作用技能需确认(409→200) OK")

        assert c.get("/api/ahs/breaker").status_code == 200
        assert c.get("/api/ahs/hooks/audit").status_code == 200
        print("[10] 治理端点(breaker/audit) OK")

        sk = {s["id"]: s for s in c.get("/api/ahs/skills").json()["skills"]}
        assert sk["calculator"]["trust"] == "high", sk["calculator"]["trust"]
        assert sk["gmail_send"]["trust"] == "low", sk["gmail_send"]["trust"]
        assert sk["wx_weather"]["trust"] == "mid"
        print("[10b] 信任分级 high/mid/low OK")

        c.post("/api/ahs/policy/deny", json={"id": "wx_weather"})
        r = c.post("/api/ahs/skills/wx_weather/test", json={"args": {}, "confirmed": True}).json()
        assert r["status"] == "blocked" and "封杀" in r["message"], r
        assert next(s for s in c.get("/api/ahs/skills").json()["skills"] if s["id"] == "wx_weather")["blocked"] is True
        assert "wx_weather" in c.get("/api/ahs/policy").json()["deny"]
        assert c.delete("/api/ahs/policy/deny/wx_weather").status_code == 200
        assert "wx_weather" not in c.get("/api/ahs/policy").json()["deny"]
        assert next(s for s in c.get("/api/ahs/skills").json()["skills"] if s["id"] == "wx_weather")["blocked"] is False
        print("[10c] deny 封杀/解封 kill-switch OK")

        assert c.delete("/api/ahs/skills/wx_weather").status_code == 200
        r = c.get("/api/ahs/skills"); assert "wx_weather" not in {s["id"] for s in r.json()["skills"]}
        print("[11] 删除用户技能 OK")

    from foxclaw import Foxclaw
    os2 = Foxclaw(data_dir=_DATA)
    assert os2.skill_registry.get("send_x") is not None, "重启后用户技能丢失"
    assert os2.skill_registry.get("send_x").source == "user"
    print("[12] 重启后用户技能仍在(持久化 round-trip) OK")

    print("\n工具中心第1期验证全部通过")

if __name__ == "__main__":
    test_tool_center()
