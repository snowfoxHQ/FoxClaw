"""领域内核端点(B 阶段三内核):财务 / 学习 / 行程 + 多格式文件导入。

从 foxclaw/api.py 原样搬移(纯搬移,零行为变更)。共享 OS 实例经 `_api._os_instance`
晚绑定读取;路由用 `@app` 装饰器在 import 时注册。
"""

import os
import json
import asyncio

from typing import Optional

from fastapi import HTTPException, UploadFile, File
from pydantic import BaseModel

import foxclaw.api as _api
from foxclaw.core.paths import resolve_import_path
from .. import app


# ── 领域内核端点（B 阶段三内核）─────────────────────────────

class ImportPayload(BaseModel):
    csv_path: Optional[str] = None
    rows: Optional[list] = None     # 也可直接传记录数组


def _resolve_csv_path(raw: str) -> str:
    """把请求里的 csv_path 收敛到允许目录内(默认 FOXCLAW_DATA_DIR)。

    此前 csv_path 原样进 import_csv → open(),等于给 HTTP 面开放「读本机任意文件」,
    且内容会落入知识库/账本再从 /api 读回。库调用(CLI/测试/示例)不走这里:
    那是本地可信代码,本身就有文件权限,加限制只破坏用法不增加安全。
    """
    try:
        return str(resolve_import_path(raw))
    except ValueError as e:
        raise HTTPException(400, f"csv_path 不允许: {e}")


@app.get("/api/finance/analysis")
async def finance_analysis(period: Optional[str] = None,
                           current_balance: Optional[float] = None):
    """财务内核综合分析:预算/异常/订阅/烧钱/守门人裁决。"""
    try:
        return _api.os_instance().finance.analyze_finances(
            period=period, current_balance=current_balance)
    except Exception as e:
        raise HTTPException(500, f"财务分析失败: {e}")


@app.post("/api/finance/import")
async def finance_import(p: ImportPayload):
    """导入交易数据(CSV 路径或记录数组)。"""
    rt = _api.os_instance().finance_runtime
    src = _resolve_csv_path(p.csv_path) if p.csv_path else ""
    try:
        if src:
            n = rt.import_csv(src)
        elif p.rows:
            from foxclaw.finance import Transaction
            n = 0
            for row in p.rows:
                # save=False:整批导完再落盘一次,别在循环里做 N 次磁盘 IO
                rt.add_transaction(Transaction(**row), run_gatekeeper=False, save=False)
                n += 1
            if n:
                rt.save()
        else:
            return {"imported": 0, "note": "未提供数据"}
        return {"imported": n, "status": rt.status()}
    except Exception as e:
        raise HTTPException(400, f"导入失败: {e}")


# ── 自然语言记账(第一线)────────────────────────────────────

class RecordPayload(BaseModel):
    text: str
    account: str = "default"
    dry_run: bool = False           # 只解析预览,不落账


class FxPayload(BaseModel):
    currency: str
    rate: float


@app.post("/api/finance/record")
async def finance_record(p: RecordPayload):
    """一句话记账:「今天买了一件衣服花了 105 块」/「明天交房租 3000」。"""
    rt = _api.os_instance().finance_runtime
    try:
        return rt.record_text(p.text, account=p.account, dry_run=p.dry_run)
    except Exception as e:
        raise HTTPException(400, f"记账失败: {e}")


@app.get("/api/finance/ledger")
async def finance_ledger(period: Optional[str] = None):
    """记账本:按月汇总(已发生/计划、按天、按分类、按币种)。"""
    rt = _api.os_instance().finance_runtime
    try:
        return rt.ledger(period)
    except Exception as e:
        raise HTTPException(500, f"记账本汇总失败: {e}")


@app.get("/api/finance/fx")
async def finance_fx():
    """当前汇率表(本位币 CNY)。"""
    return _api.os_instance().finance_runtime.fx.snapshot()


@app.post("/api/finance/fx")
async def finance_set_fx(p: FxPayload):
    """设置某个币种的记账汇率(比如刷卡当天的实际结算价)。"""
    try:
        return _api.os_instance().finance_runtime.fx.set_rate(p.currency, p.rate)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/learning/analysis")
async def learning_analysis():
    """学习内核综合分析:掌握度/遗忘风险/知识图谱/学习策略。"""
    try:
        return _api.os_instance().office.analyze_learning()
    except Exception as e:
        raise HTTPException(500, f"学习分析失败: {e}")


@app.post("/api/learning/import")
async def learning_import(p: ImportPayload):
    """导入知识点(办公学习内核)。learn() 内部已把内容存进 HKC(连续 ConceptKU,知识真源),
    不再另做桥接。"""
    rt = _api.os_instance().learning_runtime
    src = _resolve_csv_path(p.csv_path) if p.csv_path else ""
    try:
        if src:
            n = rt.import_csv(src)
        elif p.rows:
            n = 0
            for row in p.rows:
                rt.learn(**row); n += 1
        else:
            return {"imported": 0, "note": "未提供数据"}
        rt.build_connections()
        return {"imported": n, "status": rt.status()}
    except Exception as e:
        raise HTTPException(400, f"导入失败: {e}")


@app.delete("/api/learning/item/{item_id}")
async def learning_delete(item_id: str):
    """删除一个学习知识点(内存+复习状态+图谱,并落盘)。"""
    rt = _api.os_instance().learning_runtime
    ok = rt.delete(item_id)
    return {"deleted": ok, "status": rt.status()}


class NotePayload(BaseModel):
    item_id: str
    note: str


@app.post("/api/learning/note")
async def learning_note(p: NotePayload):
    """给一个学习知识点追加一条笔记(有内容才存;没新增不写盘)。"""
    rt = _api.os_instance().learning_runtime
    r = rt.add_note(p.item_id, p.note)
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/itinerary/plan")
async def itinerary_plan(mode: str = "driving"):
    """行程内核规划:路线优化/可达性/冲突/守门人 + 高德可视化数据。"""
    try:
        return _api.os_instance().office.plan_itinerary(mode=mode)
    except Exception as e:
        raise HTTPException(500, f"行程规划失败: {e}")


@app.post("/api/itinerary/import")
async def itinerary_import(p: ImportPayload):
    """导入行程点。"""
    rt = _api.os_instance().itinerary_runtime
    src = _resolve_csv_path(p.csv_path) if p.csv_path else ""
    try:
        if src:
            n = rt.import_csv(src)
        elif p.rows:
            n = 0
            for row in p.rows:
                rt.add_waypoint(**row); n += 1
        else:
            return {"imported": 0, "note": "未提供数据"}
        return {"imported": n, "status": rt.status()}
    except Exception as e:
        raise HTTPException(400, f"导入失败: {e}")


# ── 领域导入:超越 csv_path，支持上传 csv / xlsx / json ─────────────────────
# 三领域运行时的 import_csv 是"列→类型转换"的唯一权威。多格式文件在此统一解析成 rows,
# 再桥接成运行时认识的临时 CSV → 复用 import_csv 的转换逻辑(不碰运行时,单一真源)。
_IMPORT_MAX_BYTES = 10 * 1024 * 1024
_IMPORT_COLUMNS = {
    "finance":   ["amount", "category", "entity", "account", "timestamp", "tags"],
    "learning":  ["content", "topic", "source", "tags"],
    "itinerary": ["name", "address", "lng", "lat", "start_time", "duration_min", "city"],
}


def _parse_import_rows(filename: str, data: bytes) -> list:
    """把上传的 csv / xlsx / json 统一解析成 [dict]。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".json":
        obj = json.loads(data.decode("utf-8"))
        rows = obj.get("rows") if isinstance(obj, dict) else obj
        if not isinstance(rows, list):
            raise ValueError("JSON 应为对象数组或 {rows:[...]}")
        return [dict(r) for r in rows if isinstance(r, dict)]
    if ext in (".csv", ".txt", ""):
        import csv as _csv
        import io as _io
        text = data.decode("utf-8-sig", errors="replace")
        return list(_csv.DictReader(_io.StringIO(text)))
    if ext == ".xlsx":
        import openpyxl as _openpyxl
        import io as _io
        wb = _openpyxl.load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
        try:
            grid = list(wb.active.iter_rows(values_only=True))
        finally:
            wb.close()
        if not grid:
            return []
        headers = [str(h).strip() if h is not None else "" for h in grid[0]]
        out = []
        for r in grid[1:]:
            d = {headers[i]: ("" if v is None else v)
                 for i, v in enumerate(r) if i < len(headers) and headers[i]}
            if any(str(x).strip() for x in d.values()):
                out.append(d)
        return out
    raise ValueError(f"不支持的格式 {ext};支持 .csv .xlsx .json")


def _rows_to_temp_csv(rows: list, columns: list) -> str:
    """把任意来源 rows 写成运行时 import_csv 认识的 CSV(tags 列表 → | 连接),返回临时路径。"""
    import csv as _csv
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            rr = dict(r)
            if isinstance(rr.get("tags"), (list, tuple)):
                rr["tags"] = "|".join(str(t) for t in rr["tags"])
            w.writerow({c: ("" if rr.get(c) is None else rr.get(c)) for c in columns})
    return path


async def _domain_import_file(domain: str, runtime, file: UploadFile):
    """统一领域文件导入:解析 → 桥接 CSV → 运行时 import_csv(线程池,含阻塞 I/O)。"""
    data = await file.read()
    if len(data) > _IMPORT_MAX_BYTES:
        raise HTTPException(413, f"文件过大(上限 {_IMPORT_MAX_BYTES // 1048576}MB)")
    try:
        rows = _parse_import_rows(file.filename or "", data)
    except Exception as e:
        raise HTTPException(400, f"解析失败: {e}")
    if not rows:
        return {"imported": 0, "note": "文件无有效数据行"}
    tmp = _rows_to_temp_csv(rows, _IMPORT_COLUMNS[domain])
    try:
        n = await asyncio.to_thread(runtime.import_csv, tmp)
    except Exception as e:
        raise HTTPException(400, f"导入失败(检查列名是否匹配): {e}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return {"imported": n, "status": runtime.status()}


@app.post("/api/finance/import/file")
async def finance_import_file(file: UploadFile = File(...)):
    """上传 csv/xlsx/json 导入交易。列:amount,category,entity,account,timestamp,tags。"""
    rt = _api.os_instance().finance_runtime
    return await _domain_import_file("finance", rt, file)


@app.post("/api/learning/import/file")
async def learning_import_file(file: UploadFile = File(...)):
    """上传 csv/xlsx/json 导入知识点。列:content,topic,source,tags。"""
    rt = _api.os_instance().learning_runtime
    res = await _domain_import_file("learning", rt, file)
    try:
        rt.build_connections()
    except Exception:
        pass
    return res


@app.post("/api/itinerary/import/file")
async def itinerary_import_file(file: UploadFile = File(...)):
    """上传 csv/xlsx/json 导入行程点。列:name,address,lng,lat,start_time,duration_min,city。"""
    rt = _api.os_instance().itinerary_runtime
    return await _domain_import_file("itinerary", rt, file)
