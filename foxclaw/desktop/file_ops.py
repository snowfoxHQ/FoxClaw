"""文件读写后端：标准库 pathlib（跨平台）。"""
from pathlib import Path


def file_read_sync(path: str, limit: int = 1_000_000) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {path}"}
    if not p.is_file():
        return {"ok": False, "error": f"不是文件: {path}"}
    try:
        data = p.read_bytes()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if len(data) > limit:
        return {"ok": False, "error": f"文件过大({len(data)}B > {limit}B)，拒绝读取"}
    text = data.decode("utf-8", errors="replace")
    return {"ok": True, "path": str(p), "size": len(data), "content": text}


def file_write_sync(path: str, content: str, append: bool = False) -> dict:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with p.open(mode, encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": str(p), "size": p.stat().st_size, "append": append}


def file_delete_sync(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {path}"}
    if not p.is_file():
        return {"ok": False, "error": f"不是文件(拒绝删除目录): {path}"}
    try:
        p.unlink()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": str(p)}
