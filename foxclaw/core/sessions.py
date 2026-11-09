"""
会话存储(SessionStore)—— 让对话有历史。

背景:此前对话完全无状态,前端只在内存里存 `_chatHistory`,**刷新即丢**,也没有
"所有会话"可回看。本模块把每个会话落盘成一个 JSON 文件(`<data_dir>/sessions/<id>.json`),
提供 建/列/读/追加/改名/删 六个操作。

设计取舍:
  · 用 JSON 文件而非 SQLite —— 会话是低频写、小体量、便于人肉查看/备份/迁移;
  · **线程安全**:对话跑在 `chat-stream` 后台线程,与 FastAPI 请求线程并发 → 全局 RLock
    (与项目"任何跨线程共享状态必须加锁"的铁律一致);
  · 标题自动从首条用户消息提炼,用户也可改名;
  · 列表按 updated_at 倒序,只回轻字段(不回 messages,避免列表接口负载爆炸)。
"""
from __future__ import annotations
import json
import re
import builtins
import threading
import time
import uuid
from pathlib import Path


def _now() -> float:
    return time.time()


def derive_title(text: str, limit: int = 24) -> str:
    """从首条用户消息提炼会话标题:截到首个句读、限长。"""
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return "新会话"
    s = re.split(r"[。！？!?\n\r]", s, 1)[0].strip()
    return (s[:limit] or "新会话")


class SessionStore:
    """文件式会话存储。所有公开方法线程安全。"""

    def __init__(self, base_dir):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ── 内部 ──────────────────────────────────────────────
    def _path(self, sid: str) -> Path:
        # 只允许我们自己生成的 id 形态,杜绝 ../ 之类路径穿越
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(sid or "")):
            raise ValueError("非法会话 id")
        return self.base / f"{sid}.json"

    def _read(self, sid: str) -> dict | None:
        try:
            p = self._path(sid)
            if not p.exists():
                return None
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write(self, data: dict):
        p = self._path(data["id"])
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        tmp.replace(p)          # 原子替换,防写一半崩坏

    # ── 公开 API ──────────────────────────────────────────
    def create(self, title: str = "", agent_id: str = "supervisor") -> dict:
        sid = uuid.uuid4().hex[:12]
        data = {"id": sid, "title": (title or "新会话")[:60], "agent_id": agent_id,
                "created_at": _now(), "updated_at": _now(), "messages": []}
        with self._lock:
            self._write(data)
        return self.brief(data)

    def list(self, limit: int = 50) -> list:
        out = []
        with self._lock:
            for p in self.base.glob("*.json"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        out.append(self.brief(json.load(f)))
                except Exception:
                    continue
        out.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
        return out[:max(1, int(limit))]

    @staticmethod
    def brief(d: dict) -> dict:
        """列表用的轻字段(不含 messages)。"""
        msgs = d.get("messages") or []
        last = ""
        for m in reversed(msgs):
            if m.get("content"):
                last = str(m["content"])[:60]
                break
        return {"id": d.get("id"), "title": d.get("title") or "新会话",
                "agent_id": d.get("agent_id") or "supervisor",
                "created_at": d.get("created_at"), "updated_at": d.get("updated_at"),
                "message_count": len(msgs), "last": last}

    def get(self, sid: str) -> dict | None:
        with self._lock:
            return self._read(sid)

    def append(self, sid: str, role: str, content: str,
               attachments: builtins.list | None = None) -> bool:
        """追加一条消息;会话不存在则忽略。首条用户消息会顺带定标题。
        attachments(可选)= 聊天附件元数据 [{name,size,mime,url}];只落盘轻字段(不存 text,
        避免会话文件随附件正文膨胀),供「会话历史」回看时渲染附件卡片。"""
        if role not in ("user", "assistant") or not content:
            return False
        with self._lock:
            d = self._read(sid)
            if d is None:
                return False
            msgs = d.setdefault("messages", [])
            msg = {"role": role, "content": str(content), "ts": _now()}
            if attachments:
                atts = [
                    {"name": str(a.get("name", ""))[:200],
                     "size": int(a.get("size") or 0),
                     "mime": str(a.get("mime", ""))[:100],
                     "url": str(a.get("url", ""))[:500]}
                    for a in attachments if isinstance(a, dict)
                ]
                if atts:
                    msg["attachments"] = atts
            msgs.append(msg)
            # 标题仍是默认值时,用首条用户消息提炼
            if role == "user" and (not d.get("title") or d["title"] == "新会话"):
                d["title"] = derive_title(content)
            d["updated_at"] = _now()
            self._write(d)
            return True

    def rename(self, sid: str, title: str) -> bool:
        with self._lock:
            d = self._read(sid)
            if d is None:
                return False
            d["title"] = (title or "新会话")[:60]
            d["updated_at"] = _now()
            self._write(d)
            return True

    def delete(self, sid: str) -> bool:
        with self._lock:
            try:
                p = self._path(sid)
                if p.exists():
                    p.unlink()
                    return True
            except Exception:
                pass
            return False
