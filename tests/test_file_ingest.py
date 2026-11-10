"""
文件摄入打通交互层：docx/xlsx 加载器 + KDE 后缀分派 + 上传路由格式门。

离线可跑：加载器是确定性纯解析（无 LLM）；KDE 分派用假抽取器验证
「新后缀 → loader → chunker → packager → KU」链路，不调真实模型。
真实 LLM 抽取质量不在离线回归内（与 test_hkc 一致哲学）。
"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "hkc"))


def test_file_ingest():
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        os.environ.pop(k, None)
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from docx import Document
    from openpyxl import Workbook
    from hkc_kde.loaders.office_loader import DocxLoader, XlsxLoader
    from hkc_kde.chunker import Chunker
    from hkc_kde.models import ExtractionResult, RawExtraction

    print("=" * 60); print("文件摄入（docx/xlsx）打通验证"); print("=" * 60)

    TMP = Path(tempfile.mkdtemp(prefix="foxclaw_fileingest_"))

    def _make_docx() -> Path:
        p = TMP / "sample.docx"
        doc = Document()
        doc.add_heading("第一章 引言", level=1)
        doc.add_paragraph("Foxclaw 是一个认知代理操作系统，由五个 Agent 组成。")
        t = doc.add_table(rows=2, cols=2)
        t.rows[0].cells[0].text = "字段"; t.rows[0].cells[1].text = "取值"
        t.rows[1].cells[0].text = "版本"; t.rows[1].cells[1].text = "v1.6"
        doc.add_heading("第二章 架构", level=1)
        doc.add_paragraph("知识核心负责长期记忆的沉淀与演化。")
        doc.save(str(p))
        return p

    def _make_xlsx() -> Path:
        p = TMP / "sample.xlsx"
        wb = Workbook()
        ws = wb.active; ws.title = "财务"
        ws.append(["科目", "金额"]); ws.append(["收入", 100]); ws.append(["支出", 40])
        ws2 = wb.create_sheet("备注")
        ws2.append(["说明", "季度汇总"])
        wb.save(str(p)); wb.close()
        return p

    pd = DocxLoader().load(str(_make_docx()))
    assert pd.doc_type == "DOCX", pd.doc_type
    titles = [c.title for c in pd.chapters]
    assert "第一章 引言" in titles and "第二章 架构" in titles, titles
    alltext = "\n".join(s.content for c in pd.chapters for s in c.sections)
    assert "五个 Agent" in alltext, "正文段落丢失"
    assert "版本 | v1.6" in alltext, "表格行未转文本"
    ch1 = [c for c in pd.chapters if c.title == "第一章 引言"][0]
    ch2 = [c for c in pd.chapters if c.title == "第二章 架构"][0]
    assert any("版本 | v1.6" in s.content for s in ch1.sections), "表格未归到出现位置的第一章"
    assert not any("版本" in s.content for s in ch2.sections), "表格错误归到末章第二章"
    print(f"[1] DocxLoader（{len(pd.chapters)} 章，标题切分 + 表格按序归章）OK")

    px = XlsxLoader().load(str(_make_xlsx()))
    assert px.doc_type == "XLSX", px.doc_type
    sheet_titles = [c.title for c in px.chapters]
    assert "财务" in sheet_titles and "备注" in sheet_titles, sheet_titles
    xtext = "\n".join(s.content for c in px.chapters for s in c.sections)
    assert "收入 | 100" in xtext and "季度汇总" in xtext, xtext
    print(f"[2] XlsxLoader（{len(px.chapters)} 表→章，行合并）OK")

    chunks = Chunker().chunk(pd)
    assert chunks and any("Agent" in ch.content for ch in chunks), "docx 未切出有效 chunk"
    print(f"[3] Chunker 消费 docx（{len(chunks)} chunk）OK")

    def _build_kde():
        from hkc_core.utils.id_gen import IDGenerator
        from hkc_core.graph.sqlite_store import SQLiteGraphStore
        from hkc_kep.event_bus import EventBus
        from hkc_kde.kde import KnowledgeDigestEngine
        d = Path(tempfile.mkdtemp(prefix="foxclaw_kde_"))
        store = SQLiteGraphStore(str(d / "hkc.db"))
        bus = EventBus(str(d / "events.db"))
        idg = IDGenerator(str(d / "id.db"))
        return KnowledgeDigestEngine(store, bus, idg, provider="anthropic")

    class _FakeExtractor:
        """每个 chunk 抽出一个实体，捕获收到的 chunk 供断言。"""
        def __init__(self): self.seen = []
        def extract(self, chunks, progress_cb=None):
            self.seen = list(chunks)
            doc_id = chunks[0].doc_id if chunks else ""
            raw = RawExtraction(entities=[{"name": "Foxclaw", "type": "Person"}])
            return ExtractionResult(doc_id=doc_id, source=doc_id, items=[raw])

    for suffix, maker in ((".docx", _make_docx), (".xlsx", _make_xlsx)):
        kde = _build_kde()
        fake = _FakeExtractor(); kde.extractor = fake
        kus = kde.ingest_file(str(maker()), source=f"user{suffix}")
        assert fake.seen, f"{suffix}: 抽取器未收到 chunk（分派断裂）"
        assert kus, f"{suffix}: 未写入任何 KU"
    print("[4] KDE 分派 .docx/.xlsx → loader → chunker → KU 全链路 OK")

    kde = _build_kde()
    bad = TMP / "x.pptx"; bad.write_bytes(b"PK\x03\x04not really")
    try:
        kde.ingest_file(str(bad))
        raise AssertionError("应对 .pptx 抛不支持错误")
    except ValueError as e:
        assert ".pptx" in str(e) or "不支持" in str(e), str(e)
    print("[5] 不支持格式（.pptx）明确 ValueError OK")

    from fastapi.testclient import TestClient
    from foxclaw.api import app
    with TestClient(app) as c:
        r = c.post("/knowledge/ingest/file",
                   files={"file": ("bad.pptx", b"x", "application/octet-stream")})
        assert r.status_code == 415, (r.status_code, r.text)
        assert ".docx" in r.text and ".xlsx" in r.text, "415 提示未含新格式"
        with open(_make_docx(), "rb") as fh:
            r2 = c.post("/knowledge/ingest/file",
                        files={"file": ("sample.docx", fh.read(),
                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                        data={"domain": "test"})
        assert r2.status_code == 200, (r2.status_code, r2.text)
    print("[6] 上传路由格式门（.pptx→415；.docx→200）OK")

    print("\n文件摄入（docx/xlsx）验证全部通过")


if __name__ == "__main__":
    test_file_ingest()
