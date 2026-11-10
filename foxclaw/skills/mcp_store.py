"""MCP server 配置持久化。文件 <data_dir>/mcp_servers.json。

【P-Muse·第4步·安全边界】守门人模式(FOXCLAW_GUARDIAN=1)下,主进程只把
非敏感元数据(id/name/url/transport)写进这里,headers 凭证只在守门人进程。
回退路径(FOXCLAW_GUARDIAN=0,进程内直连)没有守门人保护,headers 会连同配置
一起落盘——save 检测到 headers 非空时会打印告警,避免"静默落盘凭证"。
"""
from __future__ import annotations
import json
import sys
import threading
from pathlib import Path


class McpStore:
    def __init__(self, data_dir):
        self._path = Path(data_dir) / "mcp_servers.json"
        self._lock = threading.Lock()

    def load(self) -> list:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8")).get("servers", [])
        except Exception:
            return []

    def save(self, servers: list):
        with self._lock:
            try:
                if any(dict(s.get("headers") or {}) for s in servers):
                    print("[McpStore] ⚠ 检测到 headers 凭证,将明文落盘到 "
                          f"{self._path.name}(当前为回退路径/未启用守门人)。",
                          file=sys.stderr, flush=True)
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"servers": servers}, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self._path)
            except Exception:
                pass
