"""
ThoughtChain — 带 Harness 标注的推理图
每个节点是一个推理步骤，边是因果依赖
Harness 校验结果作为节点属性附着其上
"""

import os
import sqlite3
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Literal, Optional
from pathlib import Path


NodeType = Literal[
    "goal",
    "reasoning",
    "memory_retrieval",
    "tool_intent",
    "tool_use",
    "plan",
    "decision",
    "policy_update",
    "consolidation",
    "reflection",
]

HarnessStatus = Literal["BASELINE", "PASS", "WARN", "BLOCK", "PENDING", "REGRESSION"]


@dataclass
class ThoughtNode:
    """ThoughtChain 的基本单元"""
    id: str
    session_id: str
    agent_id: str
    type: NodeType
    content: str
    conf: float
    mem_refs: list[str]
    parent_ids: list[str]
    harness_status: HarnessStatus
    harness_reason: Optional[str]
    harness_delta: Optional[float]
    dominant_dim: Optional[str]
    tool_name: Optional[str]
    entities: list[str]
    timestamp: str
    metadata: dict

    @classmethod
    def create(
        cls,
        session_id: str,
        agent_id: str,
        type: NodeType,
        content: str,
        conf: float = 1.0,
        mem_refs: list[str] | None = None,
        parent_ids: list[str] | None = None,
        tool_name: str | None = None,
        entities: list[str] | None = None,
        metadata: dict | None = None,
    ) -> "ThoughtNode":
        return cls(
            id=f"tn_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            agent_id=agent_id,
            type=type,
            content=content,
            conf=conf,
            mem_refs=mem_refs or [],
            parent_ids=parent_ids or [],
            harness_status="PENDING",
            harness_reason=None,
            harness_delta=None,
            dominant_dim=None,
            tool_name=tool_name,
            entities=entities or [],
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ThoughtNode":
        return cls(**d)


class ThoughtChain:
    """
    ThoughtChain 存储与查询引擎
    底层使用 SQLite，接口设计为对外标准化可替换
    """

    def __init__(self, db_path: str = "./foxclaw_data/thoughtchain.db"):
        self.db_path = Path(db_path).resolve()
        os.makedirs(self.db_path.parent, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS thought_nodes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    conf REAL NOT NULL,
                    mem_refs TEXT NOT NULL,
                    parent_ids TEXT NOT NULL,
                    harness_status TEXT NOT NULL,
                    harness_reason TEXT,
                    harness_delta REAL,
                    dominant_dim TEXT,
                    tool_name TEXT,
                    entities TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON thought_nodes(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent ON thought_nodes(agent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_harness ON thought_nodes(harness_status)")

    @contextmanager
    def _conn(self):
        """新建一条 SQLite 连接(每次调用即建即弃,不跨线程复用连接对象)。

        并发加固:ThoughtChain 是**五个 Agent 共享**的审计存储,而写入发生在
        `_call_llm` 的 `asyncio.to_thread` 工作线程 + FastAPI 请求线程里,必然跨线程并发写。
        SQLite 默认 busy_timeout=0,锁竞争时会立刻抛 `OperationalError: database is locked`。
          · timeout=5.0    —— 即 busy_timeout,写者遇锁最多等 5s 而非立刻报错;
          · journal_mode=WAL —— 单写多读、读写不互斥,大幅降低锁竞争(审计读多写多)。
        WAL 是数据库级持久设置,重复设置幂等无害。

        生命周期语义:`with self._conn() as conn:` 退出时——正常则 commit,异常则
        rollback,并**无论正常/异常都 close**。不能依赖 sqlite3.Connection 自带的
        `with`(它退出只 commit/rollback 不 close,WAL 的 -wal/-shm 句柄会残留,
        Windows 上删库会 WinError 32);也不能只用 closing()(它只 close 不 commit,
        会把 INSERT/UPDATE 回滚导致并发写全丢)。
        """
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add_node(self, node: ThoughtNode) -> ThoughtNode:
        """写入节点，返回写入后的节点（Harness 标注前状态）"""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO thought_nodes VALUES (
                    :id, :session_id, :agent_id, :type, :content, :conf,
                    :mem_refs, :parent_ids, :harness_status, :harness_reason,
                    :harness_delta, :dominant_dim, :tool_name, :entities,
                    :timestamp, :metadata
                )
            """, {
                **node.to_dict(),
                "mem_refs": json.dumps(node.mem_refs),
                "parent_ids": json.dumps(node.parent_ids),
                "entities": json.dumps(node.entities),
                "metadata": json.dumps(node.metadata),
            })
        return node

    def update_harness(
        self,
        node_id: str,
        status: HarnessStatus,
        reason: str | None = None,
        delta: float | None = None,
        dominant_dim: str | None = None,
    ):
        """Harness 校验完成后更新节点标注"""
        with self._conn() as conn:
            conn.execute("""
                UPDATE thought_nodes
                SET harness_status=?, harness_reason=?, harness_delta=?, dominant_dim=?
                WHERE id=?
            """, (status, reason, delta, dominant_dim, node_id))

    def get_node(self, node_id: str) -> Optional[ThoughtNode]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM thought_nodes WHERE id=?", (node_id,)
            ).fetchone()
        return self._row_to_node(row) if row else None

    def get_chain(self, session_id: str, agent_id: str | None = None) -> list[ThoughtNode]:
        """获取一个会话的完整推理链，按时间排序"""
        with self._conn() as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM thought_nodes WHERE session_id=? AND agent_id=? ORDER BY timestamp",
                    (session_id, agent_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM thought_nodes WHERE session_id=? ORDER BY timestamp",
                    (session_id,)
                ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_blocked_nodes(self, agent_id: str | None = None, limit: int = 50) -> list[ThoughtNode]:
        """获取被 Harness 截断的节点，用于审计"""
        with self._conn() as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM thought_nodes WHERE harness_status='BLOCK' AND agent_id=? ORDER BY timestamp DESC LIMIT ?",
                    (agent_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM thought_nodes WHERE harness_status='BLOCK' ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def search_nodes(self, keyword: str, agent_id: str | None = None,
                     limit: int = 20) -> list[ThoughtNode]:
        """按关键词在推理节点内容里检索(LIKE 子串匹配)。供 HSR 的 AgentSourcePort
        把「各 Agent 想过/判过什么」连通进内部搜索。只读。"""
        kw = (keyword or "").strip()
        if not kw:
            return []
        like = f"%{kw}%"
        with self._conn() as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM thought_nodes WHERE content LIKE ? AND agent_id=? "
                    "ORDER BY timestamp DESC LIMIT ?", (like, agent_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM thought_nodes WHERE content LIKE ? "
                    "ORDER BY timestamp DESC LIMIT ?", (like, limit)
                ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_delta_trend(self, agent_id: str, limit: int = 100) -> list[dict]:
        """获取漂移趋势数据，用于前端可视化"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT timestamp, harness_delta, harness_status, dominant_dim
                FROM thought_nodes
                WHERE agent_id=? AND harness_delta IS NOT NULL
                ORDER BY timestamp DESC LIMIT ?
            """, (agent_id, limit)).fetchall()
        return [
            {"timestamp": r[0], "delta": r[1], "status": r[2], "dim": r[3]}
            for r in rows
        ]

    def get_summary(self, agent_id: str) -> dict:
        """获取 Agent 的 ThoughtChain 健康摘要"""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM thought_nodes WHERE agent_id=?", (agent_id,)
            ).fetchone()[0]
            blocked = conn.execute(
                "SELECT COUNT(*) FROM thought_nodes WHERE agent_id=? AND harness_status='BLOCK'",
                (agent_id,)
            ).fetchone()[0]
            warned = conn.execute(
                "SELECT COUNT(*) FROM thought_nodes WHERE agent_id=? AND harness_status='WARN'",
                (agent_id,)
            ).fetchone()[0]
            avg_conf = conn.execute(
                "SELECT AVG(conf) FROM thought_nodes WHERE agent_id=? AND harness_status='PASS'",
                (agent_id,)
            ).fetchone()[0] or 0.0
            avg_delta = conn.execute(
                "SELECT AVG(harness_delta) FROM thought_nodes WHERE agent_id=? AND harness_delta IS NOT NULL",
                (agent_id,)
            ).fetchone()[0] or 0.0
        return {
            "agent_id": agent_id,
            "total_nodes": total,
            "blocked_nodes": blocked,
            "warned_nodes": warned,
            "block_rate": blocked / total if total > 0 else 0.0,
            "avg_confidence": round(avg_conf, 3),
            "avg_delta": round(avg_delta, 3),
        }

    def _row_to_node(self, row) -> ThoughtNode:
        cols = [
            "id", "session_id", "agent_id", "type", "content", "conf",
            "mem_refs", "parent_ids", "harness_status", "harness_reason",
            "harness_delta", "dominant_dim", "tool_name", "entities",
            "timestamp", "metadata"
        ]
        d = dict(zip(cols, row))
        for field, default in (("mem_refs", []), ("parent_ids", []),
                               ("entities", []), ("metadata", {})):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = default
        return ThoughtNode(**d)
