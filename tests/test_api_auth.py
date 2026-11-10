"""
API 可选鉴权回归

验证 FOXCLAW_API_KEY:
  · 未设 → /api/* 开放(本地默认,既有行为不变);
  · 设了 → /api/* 与 /ws/* 需带正确 X-API-Key,否则 401;前端与 /docs 始终开放。
中间件每请求读环境变量,故可在同一 TestClient 内切换验证。
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["FOXCLAW_SKIP_DOTENV"] = "1"

import tempfile
os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_test_")
from fastapi.testclient import TestClient
from foxclaw.api import app


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def main():
    print("=" * 56)
    print("API 可选鉴权回归")
    print("=" * 56)
    os.environ.pop("FOXCLAW_API_KEY", None)
    with TestClient(app) as c:
        print("\n【1】未设 FOXCLAW_API_KEY:开放(本地默认)")
        check(c.get("/api/status").status_code == 200, "/api/status 开放(200)")

        print("\n【2】设了 FOXCLAW_API_KEY:/api/* 受保护")
        os.environ["FOXCLAW_API_KEY"] = "secret-123"
        check(c.get("/api/status").status_code == 401, "无 X-API-Key → 401")
        check(c.get("/api/status", headers={"X-API-Key": "wrong"}).status_code == 401,
              "错误 key → 401")
        check(c.get("/api/status", headers={"X-API-Key": "secret-123"}).status_code == 200,
              "正确 key → 200")

        print("\n【3】WebSocket 鉴权(端点内 ?key=,http 中间件管不到 ws)")
        rejected = False
        try:
            with c.websocket_connect("/ws/harness"):
                pass
        except Exception:
            rejected = True
        check(rejected, "WS 无 key → 握手被拒")
        connected = False
        try:
            with c.websocket_connect("/ws/harness?key=secret-123"):
                connected = True
        except Exception:
            connected = False
        check(connected, "WS 带正确 ?key= → 连上")

        print("\n【4】开鉴权时前端 / 文档仍开放")
        check(c.get("/docs").status_code == 200, "/docs 无需 key(200)")
        r_root = c.get("/")
        check(r_root.status_code in (200, 404), f"/ 非 401(前端不被 key 挡, {r_root.status_code})")

        print("\n【5】撤销 key → 恢复开放")
        os.environ.pop("FOXCLAW_API_KEY", None)
        check(c.get("/api/status").status_code == 200, "撤 key 后 /api/status 恢复开放")

    print("\n" + "=" * 56)
    print("API 鉴权回归通过")
    print("=" * 56)


def test_api_auth():
    main()


if __name__ == "__main__":
    main()
