"""
hkc-core / graph / sqlite_store.py
SQLite + NetworkX 实现的 GraphStore。

SQLite   → 持久化存储，全量数据
NetworkX → 内存图，启动时从 SQLite 重建，用于路径查询
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import networkx as nx

from .base import GraphStore
from ..models.enums import KUType, ClaimStatus
from ..models.ku import (
    KU, Relation, ConflictCard,
    EntityKU, ConceptKU, FactKU, ClaimKU,
    EvidenceKU, AbilityKU,
)


# ── 序列化 / 反序列化 ────────────────────────────────────────

def _loads_dict(raw) -> dict:
    """extra 列容错解析:坏 JSON / 非 dict 一律当空字典,不抛异常打断写入。"""
    try:
        val = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return val if isinstance(val, dict) else {}


def _dump(value):
    """JSON 列(list/dict)写库前序列化,标量原样返回。"""
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value


def _ku_to_row(ku: KU) -> tuple:
    d = ku.to_dict()
    # Column order: ku_id, name, ku_type, summary, confidence, status,
    # domain, version, tags, sources, relations, extra, created_at, updated_at
    return (
        d["ku_id"],
        d["name"],
        d["ku_type"],
        d["summary"],
        d["confidence"],
        d["status"],
        d["domain"],
        d["version"],
        json.dumps(d["tags"],      ensure_ascii=False),
        json.dumps(d["sources"],   ensure_ascii=False),
        json.dumps(d["relations"], ensure_ascii=False),
        json.dumps(d["extra"],     ensure_ascii=False),
        d["created_at"],
        d["updated_at"],
    )


def _row_to_ku(row: tuple) -> KU:
    # Schema CREATE TABLE order:
    # ku_id, ku_type, name, summary, confidence, status,
    # domain, version, tags, sources, relations, extra, created_at, updated_at
    (ku_id, ku_type, name, summary, confidence, status,
     domain, version, tags, sources, relations, extra,
     created_at, updated_at) = row

    tags      = json.loads(tags)
    sources   = json.loads(sources)
    relations = json.loads(relations)
    extra     = json.loads(extra)

    base = dict(
        ku_id=ku_id, name=name, summary=summary,
        confidence=confidence, status=status, domain=domain,
        version=version, tags=tags, sources=sources,
        relations=relations, extra=extra,
        created_at=created_at, updated_at=updated_at,
    )

    t = ku_type
    if t == KUType.ENTITY.value:
        from ..models.enums import EntityType
        ku = EntityKU(**base)
        raw_etype = extra.get("entity_type", "Person")
        try:
            ku.entity_type = EntityType(raw_etype)
        except ValueError:
            ku.entity_type = EntityType.PERSON
        ku.aliases     = extra.get("aliases", [])
        ku.birth       = extra.get("birth", "")
        ku.active      = extra.get("active", True)
        ku.source_text = extra.get("source_text", "")
    elif t == KUType.CONCEPT.value:
        ku = ConceptKU(**base)
        ku.aka         = extra.get("aka", [])
        ku.definition  = extra.get("definition", "")
        ku.source_text = extra.get("source_text", "")
    elif t == KUType.FACT.value:
        ku = FactKU(**base)
        ku.statement   = extra.get("statement", "")
        ku.verifiable  = extra.get("verifiable", True)
        ku.source_ref  = extra.get("source_ref", "")
        ku.source_text = extra.get("source_text", "")
    elif t == KUType.CLAIM.value:
        ku = ClaimKU(**base)
        ku.statement      = extra.get("statement", "")
        ku.claim_status   = ClaimStatus(extra.get("claim_status", "pending"))
        ku.supports       = extra.get("supports", [])
        ku.contradicts    = extra.get("contradicts", [])
        ku.conflict_refs  = extra.get("conflict_refs", [])
        ku.source_text    = extra.get("source_text", "")
    elif t == KUType.EVIDENCE.value:
        from ..models.enums import SourceType
        ku = EvidenceKU(**base)
        ku.source       = extra.get("source", "")
        try:
            ku.source_type = SourceType(extra.get("source_type", "Book"))
        except ValueError:
            ku.source_type = SourceType.BOOK
        ku.author       = extra.get("author", "")
        ku.year         = extra.get("year", 0)
        ku.page         = extra.get("page", 0)
        ku.quote        = extra.get("quote", "")
        ku.supports     = extra.get("supports", [])
        ku.contradicts          = extra.get("contradicts", [])
    elif t == KUType.ABILITY.value:
        ku = AbilityKU(**base)
        ku.ability_key    = extra.get("ability_key", "")
        ku.skills         = extra.get("skills", [])
        ku.workflows      = extra.get("workflows", [])
        ku.coverage       = extra.get("coverage", {})
        ku.knowledge_refs = extra.get("knowledge_refs", [])
        ku.package_path   = extra.get("package_path", "")
        ku.pkg_version    = extra.get("pkg_version", "1.0.0")
    else:
        raise ValueError(f"Unknown ku_type: {t}")

    return ku


def _rel_to_row(rel: Relation) -> tuple:
    return (
        rel.rel_id, rel.from_ku, rel.to_ku,
        rel.rel_type, rel.weight, rel.source, rel.created_at,
    )


def _row_to_rel(row: tuple) -> Relation:
    rel_id, from_ku, to_ku, rel_type, weight, source, created_at = row
    return Relation(
        rel_id=rel_id, from_ku=from_ku, to_ku=to_ku,
        rel_type=rel_type, weight=weight,
        source=source or "", created_at=created_at,
    )


def _card_to_row(card: ConflictCard) -> tuple:
    return (
        card.conflict_id, card.claim_a_id, card.claim_b_id,
        card.domain, card.status.value,
        card.resolution_strategy.value,
        card.resolution_note, card.resolved_by,
        card.resolved_at, card.created_at,
    )


def _row_to_card(row: tuple) -> ConflictCard:
    from ..models.enums import ConflictStatus, ResolutionStrategy
    (conflict_id, claim_a_id, claim_b_id, domain, status,
     strategy, note, resolved_by, resolved_at, created_at) = row
    return ConflictCard(
        conflict_id=conflict_id,
        claim_a_id=claim_a_id,
        claim_b_id=claim_b_id,
        domain=domain or "",
        status=ConflictStatus(status),
        resolution_strategy=ResolutionStrategy(strategy),
        resolution_note=note or "",
        resolved_by=resolved_by,
        resolved_at=resolved_at,
        created_at=created_at,
    )


# ── SQLiteGraphStore ─────────────────────────────────────────

class SQLiteGraphStore(GraphStore):
    """
    v1 GraphStore 实现。
    - SQLite 负责持久化
    - NetworkX DiGraph 负责内存图查询（路径、邻居）
    - 所有写操作同时更新两者，保持一致
    """

    def __init__(self, db_path: str):
        self._lock = threading.RLock()
        db_file    = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self._db   = sqlite3.connect(str(db_file), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._graph: nx.DiGraph = nx.DiGraph()
        self._init_schema()
        self._load_graph()

    # ── Schema ────────────────────────────────────────────────

    def _init_schema(self):
        # Set pragmas explicitly (executescript auto-commits, pragma must be separate)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.commit()
        self._db.executescript("""
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS knowledge_units (
                ku_id       TEXT PRIMARY KEY,
                ku_type     TEXT NOT NULL,
                name        TEXT NOT NULL,
                summary     TEXT DEFAULT '',
                confidence  REAL DEFAULT 1.0,
                status      TEXT DEFAULT 'active',
                domain      TEXT DEFAULT '',
                version     INTEGER DEFAULT 1,
                tags        TEXT DEFAULT '[]',
                sources     TEXT DEFAULT '[]',
                relations   TEXT DEFAULT '[]',
                extra       TEXT DEFAULT '{}',
                created_at  TEXT,
                updated_at  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_ku_type   ON knowledge_units(ku_type);
            CREATE INDEX IF NOT EXISTS idx_ku_domain ON knowledge_units(domain);
            CREATE INDEX IF NOT EXISTS idx_ku_status ON knowledge_units(status);

            CREATE VIRTUAL TABLE IF NOT EXISTS ku_fts
            USING fts5(ku_id UNINDEXED, name, summary, domain);

            CREATE TABLE IF NOT EXISTS relations (
                rel_id      TEXT PRIMARY KEY,
                from_ku     TEXT NOT NULL,
                to_ku       TEXT NOT NULL,
                rel_type    TEXT NOT NULL,
                weight      REAL DEFAULT 1.0,
                source      TEXT DEFAULT '',
                created_at  TEXT,
                FOREIGN KEY(from_ku) REFERENCES knowledge_units(ku_id),
                FOREIGN KEY(to_ku)   REFERENCES knowledge_units(ku_id)
            );

            CREATE INDEX IF NOT EXISTS idx_rel_from ON relations(from_ku);
            CREATE INDEX IF NOT EXISTS idx_rel_to   ON relations(to_ku);
            CREATE INDEX IF NOT EXISTS idx_rel_type ON relations(rel_type);

            CREATE TABLE IF NOT EXISTS conflict_cards (
                conflict_id TEXT PRIMARY KEY,
                claim_a_id  TEXT NOT NULL,
                claim_b_id  TEXT NOT NULL,
                domain      TEXT DEFAULT '',
                status      TEXT DEFAULT 'open',
                strategy    TEXT DEFAULT 'evidence_weight',
                note        TEXT DEFAULT '',
                resolved_by TEXT,
                resolved_at TEXT,
                created_at  TEXT,
                FOREIGN KEY(claim_a_id) REFERENCES knowledge_units(ku_id),
                FOREIGN KEY(claim_b_id) REFERENCES knowledge_units(ku_id)
            );
        """)
        self._db.commit()

    def _load_graph(self):
        """启动时从 SQLite 重建 NetworkX 图。"""
        rows = self._db.execute(
            "SELECT from_ku, to_ku, rel_type, weight FROM relations"
        ).fetchall()
        for row in rows:
            self._graph.add_edge(
                row["from_ku"], row["to_ku"],
                rel_type=row["rel_type"],
                weight=row["weight"],
            )

    # ── 单个 KU ──────────────────────────────────────────────

    # patch_ku 允许直接写的普通列(JSON 列会按 list/dict 自动序列化)。
    # 刻意不含 ku_id / created_at / updated_at:前者是主键,后者由存储层维护。
    _PATCH_COLUMNS = frozenset({
        "name", "summary", "confidence", "status", "domain",
        "version", "tags", "sources", "relations",
    })

    def get(self, ku_id: str) -> Optional[KU]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM knowledge_units WHERE ku_id = ?", (ku_id,)
            ).fetchone()
            return _row_to_ku(tuple(row)) if row else None

    def put(self, ku: KU) -> None:
        with self._lock:
            ku.updated_at = datetime.now(timezone.utc).isoformat()
            self._db.execute("""
                INSERT INTO knowledge_units
                    (ku_id,name,ku_type,summary,confidence,status,domain,
                     version,tags,sources,relations,extra,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ku_id) DO UPDATE SET
                    name=excluded.name, summary=excluded.summary,
                    confidence=excluded.confidence, status=excluded.status,
                    domain=excluded.domain, version=excluded.version,
                    tags=excluded.tags, sources=excluded.sources,
                    relations=excluded.relations, extra=excluded.extra,
                    updated_at=excluded.updated_at
            """, _ku_to_row(ku))
            self._db.commit()
            # 同步 FTS：content table 不支持 OR REPLACE，用 DELETE + INSERT
            self._db.execute("DELETE FROM ku_fts WHERE ku_id=?", (ku.ku_id,))
            self._db.execute(
                "INSERT INTO ku_fts(ku_id,name,summary,domain) VALUES (?,?,?,?)",
                (ku.ku_id, ku.name, ku.summary, ku.domain)
            )
            self._db.commit()

    def patch_ku(
        self,
        ku_id:   str,
        columns: Optional[dict] = None,
        extra:   Optional[dict] = None,
    ) -> int:
        """只写「指定的列」和「extra 里指定的键」,**其余列一个字节都不动**。

        为什么需要:``put()`` 是整行 UPSERT。复习评分只改 ``extra["review"]``,走 put()
        会把整行其它列按「写入前的快照」回写 —— KEE 在同一个窗口里刚合并进去的
        sources / confidence / supports 会被悄悄冲掉;反过来,KEE 的整行写也会把复习
        进度冲掉。``extra`` 是 review / supports / claim_status / source_text 共用的
        JSON 列,所以「只改其中一两个键」的写入必须走这里,而不是 put()。

        返回受影响行数:0 表示该 KU 不存在,调用方应回退到 ``put()``(那是插入语义)。
        """
        cols = {k: v for k, v in (columns or {}).items() if k in self._PATCH_COLUMNS}
        with self._lock:
            if not cols and not extra:
                return 1 if self._db.execute(
                    "SELECT 1 FROM knowledge_units WHERE ku_id=?", (ku_id,)
                ).fetchone() else 0

            sets: list[str] = []
            args: list = []
            for k, v in cols.items():
                sets.append(f"{k}=?")
                args.append(_dump(v))
            if extra:
                row = self._db.execute(
                    "SELECT extra FROM knowledge_units WHERE ku_id=?", (ku_id,)
                ).fetchone()
                if row is None:
                    return 0
                cur = _loads_dict(row["extra"])
                cur.update(extra)
                sets.append("extra=?")
                args.append(json.dumps(cur, ensure_ascii=False))
            sets.append("updated_at=?")
            args.append(datetime.now(timezone.utc).isoformat())
            args.append(ku_id)

            cur2 = self._db.execute(
                f"UPDATE knowledge_units SET {', '.join(sets)} WHERE ku_id=?", args
            )
            self._db.commit()
            n = cur2.rowcount
            # 改了 FTS 索引覆盖的三列才需要同步,否则多一次写纯属浪费
            if n and any(k in cols for k in ("name", "summary", "domain")):
                row2 = self._db.execute(
                    "SELECT name, summary, domain FROM knowledge_units WHERE ku_id=?", (ku_id,)
                ).fetchone()
                if row2 is not None:
                    self._db.execute("DELETE FROM ku_fts WHERE ku_id=?", (ku_id,))
                    self._db.execute(
                        "INSERT INTO ku_fts(ku_id,name,summary,domain) VALUES (?,?,?,?)",
                        (ku_id, row2["name"] or "", row2["summary"] or "", row2["domain"] or ""),
                    )
                    self._db.commit()
            return n if n > 0 else 0

    def update_extra(self, ku_id: str, fn) -> Optional[dict]:
        """在锁内「读 extra → 交给 fn 算 → 写回 extra」,整段原子。

        给「读-改-写必须无竞态」的场景用(复习评分:读 review 状态 → SM-2 推演 →
        写回)。``fn(extra)`` 返回新 dict 则落盘,返回 None 表示不改。
        返回落盘后的 extra;None 表示该 KU 已不存在。
        """
        with self._lock:
            row = self._db.execute(
                "SELECT extra FROM knowledge_units WHERE ku_id=?", (ku_id,)
            ).fetchone()
            if row is None:
                return None
            extra = _loads_dict(row["extra"])
            new_extra = fn(extra)
            if new_extra is None:
                return extra
            if not isinstance(new_extra, dict):
                raise TypeError("update_extra 的回调必须返回 dict 或 None")
            self._db.execute(
                "UPDATE knowledge_units SET extra=?, updated_at=? WHERE ku_id=?",
                (json.dumps(new_extra, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(), ku_id),
            )
            self._db.commit()
            return new_extra

    def delete(self, ku_id: str) -> bool:
        """软删除(将状态置为 deleted,保留行供审计)。不级联。

        返回:
          True  — 此前该 id 存在且未被删,本次成功置为 deleted
          False — id 不存在或已经是 deleted

        阶段 B 设计:本方法**不**发事件,事件由调用方按业务语义发。
          - KEE dedup 删重复 KU → 不发(重复 KU 从未进 vector,无外部观测需要)
          - routes.delete_source 级联删 → 调用方负责 publish KNOWLEDGE_DELETED
        原因:事件是「对知识库的外部观测」,不是「存储层副作用」。
        若存储层自动发,会与 KEE / 路由层的事件总线污染(KEE 内部 delete 走 store
        不应该被业务订阅者看到,否则会误触发 indexing 重新处理同一 ID)。
        """
        with self._lock:
            cur = self._db.execute(
                "UPDATE knowledge_units SET status='deleted', "
                "updated_at=? WHERE ku_id=? AND status != 'deleted'",
                (datetime.now(timezone.utc).isoformat(), ku_id)
            )
            self._db.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        """清空整个知识库:硬删除所有 KU / 关系 / 冲突卡 / 全文索引 + 重置内存图。
        返回清空前的 KU 行数。用于"清库重来"(不可逆)。"""
        with self._lock:
            try:
                n = self._db.execute("SELECT COUNT(*) FROM knowledge_units").fetchone()[0]
            except Exception:
                n = 0
            # 先删引用方(relations 外键指向 knowledge_units),knowledge_units 放最后,
            # 否则先删父表会触发 FOREIGN KEY constraint failed。
            for tbl in ("relations", "conflict_cards", "ku_fts", "knowledge_units"):
                try:
                    self._db.execute(f"DELETE FROM {tbl}")
                except Exception:
                    pass   # 个别表(如 ku_fts)在旧库可能不存在
            self._db.commit()
            self._graph.clear()
        return n

    # ── 批量 ─────────────────────────────────────────────────

    def batch_write(self, kus: list[KU]) -> None:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            rows = []
            for ku in kus:
                ku.updated_at = now
                rows.append(_ku_to_row(ku))
            self._db.executemany("""
                INSERT INTO knowledge_units
                    (ku_id,name,ku_type,summary,confidence,status,domain,
                     version,tags,sources,relations,extra,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ku_id) DO UPDATE SET
                    name=excluded.name, summary=excluded.summary,
                    confidence=excluded.confidence, status=excluded.status,
                    domain=excluded.domain, version=excluded.version,
                    tags=excluded.tags, sources=excluded.sources,
                    relations=excluded.relations, extra=excluded.extra,
                    updated_at=excluded.updated_at
            """, rows)
            self._db.commit()
            # 同步 FTS
            for ku in kus:
                self._db.execute("DELETE FROM ku_fts WHERE ku_id=?", (ku.ku_id,))
                self._db.execute(
                    "INSERT INTO ku_fts(ku_id,name,summary,domain) VALUES (?,?,?,?)",
                    (ku.ku_id, ku.name, ku.summary, ku.domain)
                )
            self._db.commit()

    # ── 查询 ─────────────────────────────────────────────────

    def query_by_type(
        self,
        ku_type: KUType,
        domain: str = "",
        status: str = "active",
        limit: int = 100,
    ) -> list[KU]:
        with self._lock:
            if domain:
                rows = self._db.execute(
                    "SELECT * FROM knowledge_units "
                    "WHERE ku_type=? AND domain=? AND status=? LIMIT ?",
                    (ku_type.value, domain, status, limit)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM knowledge_units "
                    "WHERE ku_type=? AND status=? LIMIT ?",
                    (ku_type.value, status, limit)
                ).fetchall()
            return [_row_to_ku(tuple(r)) for r in rows]

    def query_by_domain(self, domain: str, limit: int = 200) -> list[KU]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM knowledge_units "
                "WHERE domain=? AND status!='deleted' LIMIT ?",
                (domain, limit)
            ).fetchall()
            return [_row_to_ku(tuple(r)) for r in rows]

    def search_by_name(self, name: str, fuzzy: bool = True) -> list[KU]:
        with self._lock:
            if fuzzy:
                rows = self._db.execute(
                    "SELECT ku.* FROM knowledge_units ku "
                    "JOIN ku_fts ON ku.ku_id = ku_fts.ku_id "
                    "WHERE ku_fts MATCH ? AND ku.status!='deleted' LIMIT 20",
                    (name,)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM knowledge_units "
                    "WHERE name=? AND status!='deleted'",
                    (name,)
                ).fetchall()
            return [_row_to_ku(tuple(r)) for r in rows]

    # ── 关系 ─────────────────────────────────────────────────

    def add_relation(self, rel: Relation) -> None:
        with self._lock:
            self._db.execute("""
                INSERT OR IGNORE INTO relations
                    (rel_id,from_ku,to_ku,rel_type,weight,source,created_at)
                VALUES (?,?,?,?,?,?,?)
            """, _rel_to_row(rel))
            self._db.commit()
            self._graph.add_edge(
                rel.from_ku, rel.to_ku,
                rel_type=rel.rel_type,
                weight=rel.weight,
            )

    def batch_add_relations(self, rels: "list[Relation]") -> None:
        """批量写关系：一次 executemany + 单次 commit，再同步内存图。

        替代逐条 add_relation（每条一次 commit=一次 fsync）：大文档关系发现产出
        上千条关系时，逐条 commit 的 fsync 风暴会让摄入 job 卡数分钟。此处一次落盘。
        """
        if not rels:
            return
        with self._lock:
            self._db.executemany("""
                INSERT OR IGNORE INTO relations
                    (rel_id,from_ku,to_ku,rel_type,weight,source,created_at)
                VALUES (?,?,?,?,?,?,?)
            """, [_rel_to_row(r) for r in rels])
            self._db.commit()
            for r in rels:
                self._graph.add_edge(
                    r.from_ku, r.to_ku,
                    rel_type=r.rel_type,
                    weight=r.weight,
                )

    def delete_relation(self, rel_id: str) -> None:
        with self._lock:
            row = self._db.execute(
                "SELECT from_ku,to_ku,rel_type FROM relations WHERE rel_id=?",
                (rel_id,)
            ).fetchone()
            self._db.execute("DELETE FROM relations WHERE rel_id=?", (rel_id,))
            self._db.commit()
            if row:
                from_ku, to_ku, _ = row[0], row[1], row[2]
                # 同步移除图中的边(若存在)
                if self._graph.has_edge(from_ku, to_ku):
                    try:
                        self._graph.remove_edge(from_ku, to_ku)
                    except Exception:
                        pass

    def redirect_relations(self, from_ku_id: str, to_ku_id: str) -> int:
        """把 from_ku_id 的所有关系重定向到 to_ku_id;丢弃自环;去重。"""
        if from_ku_id == to_ku_id:
            return 0
        with self._lock:
            rows = self._db.execute(
                "SELECT rel_id,from_ku,to_ku,rel_type,weight,source,created_at "
                "FROM relations WHERE from_ku=? OR to_ku=?",
                (from_ku_id, from_ku_id)
            ).fetchall()

        redirected = 0
        for r in rows:
            rel = _row_to_rel(tuple(r))
            new_from = to_ku_id if rel.from_ku == from_ku_id else rel.from_ku
            new_to   = to_ku_id if rel.to_ku   == from_ku_id else rel.to_ku

            # 删除旧关系(表+图)
            self.delete_relation(rel.rel_id)

            # 自环丢弃(重定向后两端相同)
            if new_from == new_to:
                continue

            # 检查是否已存在等价关系(避免重复)
            existing = self.get_relations(new_from, rel_type=rel.rel_type, direction="out")
            if any(e.to_ku == new_to for e in existing):
                continue

            rel.from_ku = new_from
            rel.to_ku   = new_to
            self.add_relation(rel)
            redirected += 1

        return redirected

    def get_relations(
        self,
        ku_id: str,
        rel_type: str = "",
        direction: str = "both",
    ) -> list[Relation]:
        with self._lock:
            if direction == "out":
                q = "SELECT * FROM relations WHERE from_ku=?"
                params = [ku_id]
            elif direction == "in":
                q = "SELECT * FROM relations WHERE to_ku=?"
                params = [ku_id]
            else:
                q = "SELECT * FROM relations WHERE from_ku=? OR to_ku=?"
                params = [ku_id, ku_id]
            if rel_type:
                q += " AND rel_type=?"
                params.append(rel_type)
            rows = self._db.execute(q, params).fetchall()
            return [_row_to_rel(tuple(r)) for r in rows]

    def get_all_relations(self, limit: int = 5000) -> list[Relation]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM relations LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_rel(tuple(r)) for r in rows]

    def neighbors(
        self,
        ku_id: str,
        rel_type: str = "",
        depth: int = 1,
    ) -> list[KU]:
        with self._lock:
            if depth == 1:
                # 直接邻居，从内存图取 ID，再去 SQLite 取完整数据
                out_nodes = list(self._graph.successors(ku_id))
                in_nodes  = list(self._graph.predecessors(ku_id))
                neighbor_ids = set(out_nodes + in_nodes)
            else:
                # BFS 多层
                neighbor_ids = set()
                frontier = {ku_id}
                for _ in range(depth):
                    next_frontier = set()
                    for node in frontier:
                        next_frontier.update(self._graph.successors(node))
                        next_frontier.update(self._graph.predecessors(node))
                    neighbor_ids.update(next_frontier)
                    frontier = next_frontier
                neighbor_ids.discard(ku_id)

            result = []
            for nid in neighbor_ids:
                ku = self.get(nid)
                if ku:
                    result.append(ku)
            return result

    def shortest_path(self, from_id: str, to_id: str) -> list[str]:
        try:
            return nx.shortest_path(self._graph, from_id, to_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    # ── Conflict Cards ────────────────────────────────────────

    def save_conflict(self, card: ConflictCard) -> None:
        with self._lock:
            self._db.execute("""
                INSERT OR REPLACE INTO conflict_cards
                    (conflict_id,claim_a_id,claim_b_id,domain,status,
                     strategy,note,resolved_by,resolved_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, _card_to_row(card))
            self._db.commit()

    def get_conflict(self, conflict_id: str) -> Optional[ConflictCard]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM conflict_cards WHERE conflict_id=?",
                (conflict_id,)
            ).fetchone()
            return _row_to_card(tuple(row)) if row else None

    def list_conflicts(
        self,
        status: str = "open",
        domain: str = "",
    ) -> list[ConflictCard]:
        with self._lock:
            if domain:
                rows = self._db.execute(
                    "SELECT * FROM conflict_cards WHERE status=? AND domain=?",
                    (status, domain)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM conflict_cards WHERE status=?", (status,)
                ).fetchall()
            return [_row_to_card(tuple(r)) for r in rows]

    def find_claims_by_domain(
        self,
        domain: str,
        status: ClaimStatus = None,
    ) -> list[ClaimKU]:
        with self._lock:
            if status:
                rows = self._db.execute(
                    "SELECT * FROM knowledge_units "
                    "WHERE ku_type='Claim' AND domain=? "
                    "AND json_extract(extra,'$.claim_status')=?",
                    (domain, status.value)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM knowledge_units "
                    "WHERE ku_type='Claim' AND domain=?",
                    (domain,)
                ).fetchall()
            return [_row_to_ku(tuple(r)) for r in rows]

    # ── 统계 ─────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            type_counts = {}
            rows = self._db.execute(
                "SELECT ku_type, COUNT(*) as cnt "
                "FROM knowledge_units WHERE status!='deleted' "
                "GROUP BY ku_type"
            ).fetchall()
            for row in rows:
                type_counts[row["ku_type"]] = row["cnt"]

            rel_count = self._db.execute(
                "SELECT COUNT(*) FROM relations"
            ).fetchone()[0]

            conflict_count = self._db.execute(
                "SELECT COUNT(*) FROM conflict_cards WHERE status='open'"
            ).fetchone()[0]

            return {
                "ku_counts":      type_counts,
                "total_kus":      sum(type_counts.values()),
                "total_relations": rel_count,
                "open_conflicts":  conflict_count,
                "graph_nodes":    self._graph.number_of_nodes(),
                "graph_edges":    self._graph.number_of_edges(),
            }

    def close(self) -> None:
        """显式关闭数据库连接。测试和应用退出时调用。"""
        self._db.close()
