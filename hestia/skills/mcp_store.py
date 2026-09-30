"""MCP server 配置持久化。文件 <data_dir>/mcp_servers.json。"""
from __future__ import annotations
import json
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
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"servers": servers}, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self._path)
            except Exception:
                pass
