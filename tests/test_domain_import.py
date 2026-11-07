"""OS 领域导入超越 csv:三领域(finance/learning/itinerary)支持上传 csv / xlsx / json。
多格式统一解析成 rows → 桥接临时 CSV → 复用各运行时 import_csv(唯一转换真源)。"""
import os, sys, io, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_domain_import():
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"
    os.environ["FOXCLAW_DATA_DIR"] = tempfile.mkdtemp(prefix="foxclaw_import_")

    import openpyxl

    print("=" * 60); print("OS 领域导入(csv/xlsx/json)验证"); print("=" * 60)

    # ── Part A:解析器单元 ─────────────────────────────────────
    from foxclaw.api import _parse_import_rows

    csv_bytes = b"amount,category,entity\n-50,food,cafe\n-20,transport,metro\n"
    rows = _parse_import_rows("t.csv", csv_bytes)
    assert len(rows) == 2 and rows[0]["amount"] == "-50" and rows[0]["category"] == "food", rows
    json_bytes = json.dumps([{"amount": -30, "category": "book"}]).encode()
    assert _parse_import_rows("t.json", json_bytes)[0]["amount"] == -30
    assert _parse_import_rows("t.json", b'{"rows":[{"amount":1}]}')[0]["amount"] == 1

    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["amount", "category", "entity"]); ws.append([-15, "coffee", "cafe"]); ws.append([-8, "snack", "shop"])
    buf = io.BytesIO(); wb.save(buf); xlsx_bytes = buf.getvalue()
    xr = _parse_import_rows("t.xlsx", xlsx_bytes)
    assert len(xr) == 2 and str(xr[0]["amount"]) == "-15" and xr[0]["category"] == "coffee", xr
    try:
        _parse_import_rows("t.pptx", b"x"); raise AssertionError("应对未支持格式报错")
    except ValueError:
        pass
    print("[1] 解析器 csv/json/xlsx + 未支持格式报错 OK")

    # ── Part B:三领域文件导入端点(TestClient)────────────────
    from fastapi.testclient import TestClient
    from foxclaw.api import app

    with TestClient(app) as c:
        # [2] finance:csv / xlsx / json 三格式各导入成功
        r = c.post("/api/finance/import/file", files={"file": ("f.csv", csv_bytes, "text/csv")})
        assert r.status_code == 200 and r.json()["imported"] == 2, r.text
        r = c.post("/api/finance/import/file",
                   files={"file": ("f.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert r.status_code == 200 and r.json()["imported"] == 2, r.text
        r = c.post("/api/finance/import/file",
                   files={"file": ("f.json", json.dumps([{"amount": -30, "category": "book", "entity": "store"}]).encode(), "application/json")})
        assert r.status_code == 200 and r.json()["imported"] == 1, r.text
        print("[2] finance 导入 csv/xlsx/json(2+2+1)OK")

        # [3] learning:csv(content,topic,tags)
        lc = "content,topic,tags\n间隔重复提升记忆,学习方法,记忆|复习\n费曼技巧,学习方法,输出\n".encode()
        r = c.post("/api/learning/import/file", files={"file": ("l.csv", lc, "text/csv")})
        assert r.status_code == 200 and r.json()["imported"] == 2, r.text
        print("[3] learning 导入 csv(含 tags 管道分隔)OK")

        # [4] itinerary:json(name/city/duration)
        ij = json.dumps([{"name": "故宫", "city": "北京", "duration_min": 120},
                         {"name": "颐和园", "city": "北京", "duration_min": 90}]).encode()
        r = c.post("/api/itinerary/import/file", files={"file": ("i.json", ij, "application/json")})
        assert r.status_code == 200 and r.json()["imported"] == 2, r.text
        print("[4] itinerary 导入 json OK")

        # [5] 未支持格式 → 400;空文件 → imported 0
        r = c.post("/api/finance/import/file", files={"file": ("x.pptx", b"x", "application/octet-stream")})
        assert r.status_code == 400, r.status_code
        r = c.post("/api/finance/import/file", files={"file": ("e.csv", b"amount,category\n", "text/csv")})
        assert r.status_code == 200 and r.json()["imported"] == 0, r.text
        print("[5] 未支持格式→400;空数据→imported 0 OK")

    print("\nOS 领域导入(csv/xlsx/json)验证通过")


if __name__ == "__main__":
    test_domain_import()
