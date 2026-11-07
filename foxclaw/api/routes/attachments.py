"""聊天附件:通用文件上传/下载(多模态渲染·步骤7)。

与「文件摄入知识库」(domain.py 的 import + HKC ingest)不同:这里是**聊天附件**——文件随问题
发给 LLM(文本类提取正文、其它降级为元数据提示),并在对话里以卡片渲染。图片多模态(images
base64)走既有 /chat/stream 的 images 通道,不经过这里。

安全铁律:
  · 文件名只留 basename + 清洗非法字符,杜绝路径穿越;
  · id 只认我们生成的 [A-Za-z0-9_-] 形态;
  · 大小 / 文本提取长度均设上限(防超大 payload / 超长上下文);
  · 附件落 `<data_dir>/attachments/`,下载按 id 定位,不信任客户端传路径。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse

import foxclaw.api as _api
from .. import app


_MAX_ATTACH_BYTES = 20 * 1024 * 1024        # 单附件上限 20MB
_MAX_TEXT_CHARS = 20000                      # 单文件提取正文上限(防超长文件撑爆上下文)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 文本类扩展名 → 直接读正文;.pdf 走 fitz(可选依赖);其余二进制 → 降级为元数据提示
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".log",
    ".py", ".js", ".ts", ".html", ".css", ".xml", ".toml", ".ini", ".sh",
    ".go", ".rs", ".java", ".c", ".cpp", ".h", ".sql", ".rb", ".php",
}


def _att_dir() -> Path:
    d = Path(_api._os_instance.data_dir) / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _decode_text(data: bytes) -> str:
    """按常见编码尝试解码,失败返回空(降级)。"""
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def _extract_text(path: Path, name: str) -> str:
    """从上传的附件提取正文:文本类直接读、PDF 用 fitz、其它返回空(前端/LLM 降级为元数据提示)。"""
    ext = Path(name).suffix.lower()
    if ext in _TEXT_EXTS:
        try:
            return _decode_text(path.read_bytes())[:_MAX_TEXT_CHARS]
        except Exception:
            return ""
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF(可选依赖 knowledge-files)
        except ImportError:
            return ""
        try:
            doc = fitz.open(str(path))
            try:
                text = ""
                for page in doc:
                    text += page.get_text()
                    if len(text) >= _MAX_TEXT_CHARS:
                        break
                return text[:_MAX_TEXT_CHARS]
            finally:
                doc.close()
        except Exception:
            return ""
    return ""


@app.post("/api/upload")
async def upload_attachment(file: UploadFile = File(...)):
    """通用聊天附件上传:落盘 + 尽力提取正文,返回 {id,name,size,mime,url,text}。
    text 为空表示不可提取(二进制/图片等),前端/LLM 降级为「文件名 + 元数据」提示。"""
    raw_name = (file.filename or "attachment").strip()
    name = Path(raw_name).name or "attachment"     # 只留 basename,去路径分隔符
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    if len(data) > _MAX_ATTACH_BYTES:
        raise HTTPException(413, f"文件过大({len(data) / 1048576:.1f}MB>{_MAX_ATTACH_BYTES // 1048576}MB)")
    aid = uuid.uuid4().hex[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._\-]", "_", name)[:120] or "attachment"
    dest = _att_dir() / f"{aid}_{safe_name}"
    dest.write_bytes(data)
    text = _extract_text(dest, name)
    return {
        "id": aid,
        "name": name,
        "size": len(data),
        "mime": (file.content_type or "application/octet-stream").lower(),
        "url": f"/api/attachments/{aid}",
        "text": text,
    }


@app.get("/api/attachments/{aid}")
async def get_attachment(aid: str):
    """下载附件。id 只认自生成形态,路径穿越到此为止。"""
    if not _ID_RE.fullmatch(aid or ""):
        raise HTTPException(400, "非法附件 id")
    d = _att_dir()
    for p in d.glob(f"{aid}_*"):
        return FileResponse(str(p), filename=p.name.split("_", 1)[1])
    raise HTTPException(404, "附件不存在")
