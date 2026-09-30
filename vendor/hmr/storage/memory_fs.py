"""
MemoryFS - 记忆文件系统
修复内容：
1. 文件写入加线程锁（防止多代理并发损坏）
2. AgentWorkspace 持久化（进程重启不丢失）
3. 原子写入（写临时文件再替换，防止写到一半崩溃）
4. 启动时重建向量索引的辅助方法
"""

import json
import threading
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from ..core.models import MemoryObject, RuntimeState, AgentWorkspace


class MemoryFS:
    """
    记忆文件系统（线程安全版本）

    目录结构：
    hmr_data/
    ├── memories/
    │   ├── concepts/
    │   ├── projects/
    │   ├── decisions/
    │   └── ...
    ├── runtimes/
    ├── workspaces/          ← 新增：AgentWorkspace 持久化
    ├── vector_store/        ← 给 VectorStore 使用
    └── index/
    """

    SCHEMA_VERSION = "1.1"

    def __init__(self, base_path: str = "./hmr_data"):
        self.base_path = Path(base_path)
        self._lock = threading.Lock()   # 全局写入锁
        self._init_directories()

    def _init_directories(self):
        dirs = [
            self.base_path / "memories" / t
            for t in ["concepts", "projects", "decisions", "runtimes",
                       "tasks", "executions", "workflows", "agent_memory", "reflections"]
        ] + [
            self.base_path / "runtimes",
            self.base_path / "workspaces",
            self.base_path / "vector_store",
            self.base_path / "index",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # 写入 schema 版本
        version_file = self.base_path / "schema_version.json"
        if not version_file.exists():
            self._atomic_write(version_file, {"version": self.SCHEMA_VERSION})

    # =========================================================================
    # MemoryObject
    # =========================================================================

    def write_memory(self, memory: MemoryObject) -> str:
        type_map = {
            "concept": "concepts", "project": "projects",
            "decision": "decisions", "runtime": "runtimes",
            "task": "tasks", "execution": "executions",
            "workflow": "workflows", "agent_memory": "agent_memory",
            "reflection": "reflections"
        }
        sub = type_map.get(memory.type, memory.type + "s")
        type_dir = self.base_path / "memories" / sub
        type_dir.mkdir(exist_ok=True)

        file_path = type_dir / f"{memory.id}.json"
        data = memory.model_dump(mode="json")

        with self._lock:
            self._atomic_write(file_path, data)
            self._update_memory_index(memory.id, memory.type, file_path)

        return str(file_path)

    def read_memory(self, memory_id: str) -> Optional[MemoryObject]:
        for type_dir in (self.base_path / "memories").iterdir():
            if not type_dir.is_dir():
                continue
            f = type_dir / f"{memory_id}.json"
            if f.exists():
                data = self._safe_read(f)
                return MemoryObject(**data) if data else None
        return None

    TYPE_MAP = {
        "concept": "concepts", "project": "projects",
        "decision": "decisions", "runtime": "runtimes",
        "task": "tasks", "execution": "executions",
        "workflow": "workflows", "agent_memory": "agent_memory",
        "reflection": "reflections"
    }

    def list_memories(self, memory_type: Optional[str] = None) -> List[MemoryObject]:
        memories = []
        base = self.base_path / "memories"
        if memory_type:
            mapped = self.TYPE_MAP.get(memory_type, memory_type + "s")
            dirs = [base / mapped]
        else:
            dirs = list(base.iterdir())
        for d in dirs:
            if not d.is_dir():
                continue
            for f in d.glob("*.json"):
                data = self._safe_read(f)
                if data:
                    try:
                        memories.append(MemoryObject(**data))
                    except Exception:
                        pass
        return memories

    def search_memories(self, query: str, memory_type: Optional[str] = None) -> List[MemoryObject]:
        """简单文本搜索（语义搜索走 VectorStore）"""
        q = query.lower()
        return [
            m for m in self.list_memories(memory_type)
            if q in m.title.lower() or q in m.content.lower()
        ]

    def delete_memory(self, memory_id: str) -> bool:
        for type_dir in (self.base_path / "memories").iterdir():
            if not type_dir.is_dir():
                continue
            f = type_dir / f"{memory_id}.json"
            if f.exists():
                with self._lock:
                    f.unlink()
                return True
        return False

    # =========================================================================
    # RuntimeState
    # =========================================================================

    def write_runtime_state(self, state: RuntimeState) -> str:
        file_path = self.base_path / "runtimes" / f"{state.runtime_id}.json"
        data = state.model_dump(mode="json")
        with self._lock:
            self._atomic_write(file_path, data)
            self._update_runtime_index(state.runtime_id, file_path)
        return str(file_path)

    def read_runtime_state(self, runtime_id: str) -> Optional[RuntimeState]:
        f = self.base_path / "runtimes" / f"{runtime_id}.json"
        data = self._safe_read(f)
        return RuntimeState(**data) if data else None

    def list_runtime_states(self) -> List[RuntimeState]:
        states = []
        for f in (self.base_path / "runtimes").glob("*.json"):
            data = self._safe_read(f)
            if data:
                try:
                    states.append(RuntimeState(**data))
                except Exception:
                    pass
        return states

    # =========================================================================
    # AgentWorkspace 持久化（修复：之前没有持久化）
    # =========================================================================

    def write_workspace(self, workspace: AgentWorkspace) -> str:
        file_path = self.base_path / "workspaces" / f"{workspace.agent_id}.json"
        data = workspace.model_dump(mode="json")
        with self._lock:
            self._atomic_write(file_path, data)
        return str(file_path)

    def read_workspace(self, agent_id: str) -> Optional[AgentWorkspace]:
        f = self.base_path / "workspaces" / f"{agent_id}.json"
        data = self._safe_read(f)
        return AgentWorkspace(**data) if data else None

    def list_workspaces(self) -> List[AgentWorkspace]:
        workspaces = []
        for f in (self.base_path / "workspaces").glob("*.json"):
            data = self._safe_read(f)
            if data:
                try:
                    workspaces.append(AgentWorkspace(**data))
                except Exception:
                    pass
        return workspaces

    def delete_workspace(self, agent_id: str) -> bool:
        f = self.base_path / "workspaces" / f"{agent_id}.json"
        if f.exists():
            with self._lock:
                f.unlink()
            return True
        return False

    # =========================================================================
    # 统计
    # =========================================================================

    def get_statistics(self) -> Dict[str, Any]:
        memories = self.list_memories()
        runtimes = self.list_runtime_states()
        workspaces = self.list_workspaces()

        type_counts: Dict[str, int] = {}
        for m in memories:
            type_counts[m.type] = type_counts.get(m.type, 0) + 1

        total_size = sum(f.stat().st_size for f in self.base_path.rglob("*.json"))

        return {
            "total_memories": len(memories),
            "memory_types": type_counts,
            "total_runtimes": len(runtimes),
            "total_workspaces": len(workspaces),
            "disk_usage_bytes": total_size,
            "disk_usage_mb": round(total_size / (1024 * 1024), 2),
            "schema_version": self.SCHEMA_VERSION
        }

    # =========================================================================
    # 辅助：原子写入 & 安全读取
    # =========================================================================

    def _atomic_write(self, file_path: Path, data: dict):
        """写临时文件再原子替换，防止写到一半崩溃导致数据损坏"""
        tmp = file_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(file_path)

    def _safe_read(self, file_path: Path) -> Optional[dict]:
        """安全读取，文件损坏时返回 None"""
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[HMR MemoryFS] 文件读取失败 {file_path}: {e}")
            return None

    def _update_memory_index(self, memory_id: str, memory_type: str, file_path: Path):
        index_file = self.base_path / "index" / "memory_index.json"
        index = {}
        if index_file.exists():
            index = self._safe_read(index_file) or {}
        index[memory_id] = {
            "type": memory_type,
            "path": str(file_path),
            "updated_at": datetime.utcnow().isoformat()
        }
        self._atomic_write(index_file, index)

    def _update_runtime_index(self, runtime_id: str, file_path: Path):
        index_file = self.base_path / "index" / "runtime_index.json"
        index = {}
        if index_file.exists():
            index = self._safe_read(index_file) or {}
        index[runtime_id] = {
            "path": str(file_path),
            "updated_at": datetime.utcnow().isoformat()
        }
        self._atomic_write(index_file, index)

    def rebuild_index(self):
        """从文件系统重建索引（索引损坏时使用）"""
        memory_index = {}
        runtime_index = {}

        for type_dir in (self.base_path / "memories").iterdir():
            if not type_dir.is_dir():
                continue
            for f in type_dir.glob("*.json"):
                memory_index[f.stem] = {
                    "type": type_dir.name,
                    "path": str(f),
                    "updated_at": datetime.utcnow().isoformat()
                }

        for f in (self.base_path / "runtimes").glob("*.json"):
            runtime_index[f.stem] = {
                "path": str(f),
                "updated_at": datetime.utcnow().isoformat()
            }

        index_dir = self.base_path / "index"
        self._atomic_write(index_dir / "memory_index.json", memory_index)
        self._atomic_write(index_dir / "runtime_index.json", runtime_index)
        print(f"[HMR MemoryFS] 索引重建完成：{len(memory_index)} 条记忆，{len(runtime_index)} 条运行时")
