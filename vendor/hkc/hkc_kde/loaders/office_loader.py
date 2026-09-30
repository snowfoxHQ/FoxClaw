"""
hkc-kde / loaders / office_loader.py
Office 文档解析器：支持 .docx（Word）/ .xlsx（Excel）。

- docx：python-docx 读取。段落按「标题样式(Heading/Title)」切章节，正文段落归入当前章节，
  表格逐行转成 "单元格 | 单元格" 文本一并纳入（很多知识就藏在 Word 表格里）。
- xlsx：openpyxl 只读+data_only 读取（取缓存值而非公式）。每个工作表 → 一个 Chapter，
  行按批合并成 Section（避免上千行切出上千个碎 Section 拖垮 chunker/LLM）。

所有格式最终转成纯文本，按 ParsedDocument 返回，后续 pipeline 与其它格式一致。
软依赖：未装 python-docx / openpyxl 时抛清晰的可操作错误（提示装 knowledge-files），
不静默产出空文档（否则用户以为摄入成功却啥都没进库）。
"""
from __future__ import annotations

import uuid
import logging
from pathlib import Path

from ..models import ParsedDocument, Chapter, Section

logger = logging.getLogger(__name__)

# 软依赖检测（缺失时在 load 抛可操作错误，而非 import 期崩溃整个 KDE）
try:
    import docx as _docx  # python-docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import openpyxl as _openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


_MIN_SECTION_CHARS = 2          # docx 段落可能很短（标题项/列表项），阈值给低些保内容
_XLSX_ROWS_PER_SECTION = 20     # 每 20 行合并为一个 Section
_XLSX_MAX_ROWS_PER_SHEET = 5000 # 单表行上限，超出截断（防超大表切出海量 chunk 烧 LLM）
_XLSX_MAX_CELL_CHARS = 500      # 单元格文本上限，防个别巨长单元格


def _flush_chapter(chapters, title, paras, counter):
    """把累积段落收敛成一个 Chapter；空则跳过。返回新的 counter。"""
    secs = [
        Section(f"SEC_{counter:04d}_{i:04d}", p)
        for i, p in enumerate(paras)
        if len(p) >= _MIN_SECTION_CHARS
    ]
    if secs:
        chapters.append(Chapter(
            chapter_id=f"CH_{counter:04d}",
            title=title,
            sections=secs,
        ))
        return counter + 1
    return counter


class DocxLoader:
    """Word .docx → ParsedDocument。按标题样式切章节，含表格文本。"""

    def load(self, path: str) -> ParsedDocument:
        if not HAS_DOCX:
            raise RuntimeError(
                "解析 .docx 需要 python-docx，未安装。请安装可选依赖："
                "pip install -e \".[knowledge-files]\"  或  pip install python-docx"
            )
        path = Path(path)
        document = _docx.Document(str(path))

        chapters: list[Chapter] = []
        current_title = ""
        current_paras: list[str] = []
        counter = 1

        def _add_para(para):
            nonlocal current_title, current_paras, counter
            text = (para.text or "").strip()
            if not text:
                return
            style = (getattr(para.style, "name", "") or "")
            if style.startswith("Heading") or style == "Title":
                # 遇到新标题：先收敛上一章，再切换标题
                counter = _flush_chapter(chapters, current_title, current_paras, counter)
                current_title = text
                current_paras = []
            else:
                current_paras.append(text)

        def _add_table(table):
            # 逐行转 "cell | cell | cell"，追加到【当前】章节（表格归它实际出现的那一节）
            for row in table.rows:
                cells = [(c.text or "").strip() for c in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    current_paras.append(line)

        # 按文档 body 子元素的真实顺序遍历，使表格归入它出现位置的章节（而非全塞末章，
        # 否则 Evidence 溯源标题失真）。用 tag 后缀判断以耐 python-docx 版本差异；
        # 任何异常回退到「先所有段落、再所有表格」的稳妥旧路径。
        try:
            from docx.text.paragraph import Paragraph as _Para
            from docx.table import Table as _Table
            body = document.element.body
            for child in body.iterchildren():
                tag = getattr(child, "tag", "")
                if tag.endswith("}p"):
                    _add_para(_Para(child, document))
                elif tag.endswith("}tbl"):
                    _add_table(_Table(child, document))
        except Exception:
            chapters.clear(); current_title = ""; current_paras = []; counter = 1
            for para in document.paragraphs:
                _add_para(para)
            for table in document.tables:
                _add_table(table)

        counter = _flush_chapter(chapters, current_title, current_paras, counter)

        if not chapters:
            # 空文档也返回一个占位章节，让上层 "文档无有效内容" 分支统一处理
            chapters = [Chapter("CH_0001", "", [])]

        return ParsedDocument(
            doc_id=f"DOC_{uuid.uuid4().hex[:8].upper()}",
            title=path.stem,
            source=str(path),
            doc_type="DOCX",
            chapters=chapters,
        )


class XlsxLoader:
    """Excel .xlsx → ParsedDocument。每工作表一章，行按批合并为 Section。"""

    def load(self, path: str) -> ParsedDocument:
        if not HAS_OPENPYXL:
            raise RuntimeError(
                "解析 .xlsx 需要 openpyxl，未安装。请安装可选依赖："
                "pip install -e \".[knowledge-files]\"  或  pip install openpyxl"
            )
        path = Path(path)
        # read_only + data_only：大表内存友好，且取缓存值而非公式串。
        wb = _openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        chapters: list[Chapter] = []
        counter = 1
        try:
            for ws in wb.worksheets:
                rows_text: list[str] = []
                for ri, row in enumerate(ws.iter_rows(values_only=True)):
                    if ri >= _XLSX_MAX_ROWS_PER_SHEET:
                        logger.warning(
                            "工作表 %s 超过 %d 行，已截断", ws.title, _XLSX_MAX_ROWS_PER_SHEET)
                        break
                    cells = []
                    for v in row:
                        if v is None:
                            continue
                        s = str(v).strip()
                        if s:
                            cells.append(s[:_XLSX_MAX_CELL_CHARS])
                    if cells:
                        rows_text.append(" | ".join(cells))

                if not rows_text:
                    continue

                # 按批合并成 Section
                secs = []
                for bi, start in enumerate(range(0, len(rows_text), _XLSX_ROWS_PER_SECTION)):
                    batch = rows_text[start:start + _XLSX_ROWS_PER_SECTION]
                    secs.append(Section(f"SEC_{counter:04d}_{bi:04d}", "\n".join(batch)))
                chapters.append(Chapter(
                    chapter_id=f"CH_{counter:04d}",
                    title=ws.title or f"Sheet{counter}",
                    sections=secs,
                ))
                counter += 1
        finally:
            # read_only 模式必须显式关，否则底层 zip 文件句柄泄漏（Windows 上还会锁文件）
            wb.close()

        if not chapters:
            chapters = [Chapter("CH_0001", "", [])]

        return ParsedDocument(
            doc_id=f"DOC_{uuid.uuid4().hex[:8].upper()}",
            title=path.stem,
            source=str(path),
            doc_type="XLSX",
            chapters=chapters,
        )
