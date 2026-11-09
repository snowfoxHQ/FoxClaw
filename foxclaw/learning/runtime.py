"""
Foxclaw 学习子系统 — Learning Runtime
学习行为操作系统:采集 → 沉淀(HMR + 双结构) → 固化(间隔重复) → 连接(知识图谱)。
对应财务 Runtime,office Agent 的"赫尔墨斯式自学"内核。
"""

import csv
import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import KnowledgeItem, ReviewState, LearningVerdict
from .spaced_repetition import SpacedRepetition
from .knowledge_graph import KnowledgeGraph


class LearningRuntime:
    """office Agent 的学习内核。"""

    def __init__(self, hmr=None, hkc_provider=None, persist_path=None):
        self.items: dict = {}
        self.review_states: dict = {}
        self.sr = SpacedRepetition()
        self.hmr = hmr
        self._hkc_provider = hkc_provider
        self._lock = threading.RLock()
        embedder = None
        if hmr is not None and hasattr(hmr, "_hmr"):
            embedder = self._make_embedder(hmr)
        self.graph = KnowledgeGraph(embedder=embedder)
        self._persist_path = Path(persist_path) if persist_path else None
        self._hkc_loaded = False
        self._load_file()
        self._ensure_hkc_loaded()

    def _make_embedder(self, hmr):
        """尝试复用 HMR 的 embedding;失败则返回 None(图谱自动降级哈希)。"""
        try:
            store = getattr(hmr._hmr, "vector_store", None)
            if store and hasattr(store, "_embed"):
                return lambda t: list(store._embed(t))
        except Exception:
            pass
        return None

    def _graph_store(self):
        """惰性拿 HKC 的 graph_store。拿不到(HKC 未装配/未配/异常)返回 None。"""
        if not self._hkc_provider:
            return None
        try:
            hkc = self._hkc_provider()
            c = getattr(hkc, "container", None) if hkc else None
            return getattr(c, "graph_store", None) if c else None
        except Exception:
            return None

    def _item_to_ku(self, item):
        """KnowledgeItem → 一个连续的 ConceptKU:source_text=全文;notes/复习状态/kind 存 extra。"""
        from hkc_core.models.ku import ConceptKU
        rs = self.review_states.get(item.id)
        ku = ConceptKU(
            ku_id=item.id,
            name=(item.topic or "学习知识点")[:80],
            summary=(item.content or "")[:200],
            tags=list(item.tags or []) + ["learning_note"],
            domain=item.topic or "learning",
            source_text=item.content or "",
        )
        ku.extra.update({
            "kind": "learning_note",
            "notes": list(item.notes or []),
            "note_source": item.source or "",
            "learn_created_at": item.created_at,
            "review": asdict(rs) if rs is not None else None,
        })
        return ku

    def _ku_to_item(self, ku):
        """HKC 的 learning_note KU → (KnowledgeItem, review_dict)。"""
        ex = getattr(ku, "extra", {}) or {}
        content = getattr(ku, "source_text", "") or ex.get("source_text", "") or ku.summary
        tags = [t for t in (ku.tags or []) if t != "learning_note"]
        item = KnowledgeItem(
            content=content,
            topic=ku.name or ku.domain or "general",
            source=ex.get("note_source", ""),
            tags=tags,
            notes=list(ex.get("notes", []) or []),
            created_at=ex.get("learn_created_at") or ku.created_at,
            id=ku.ku_id,
        )
        return item, ex.get("review")

    def _load_file(self):
        """把离线队列 / 旧 learning.json 载入内存(__init__ 时 HKC 常未就绪,先撑住 UI)。"""
        if not (self._persist_path and self._persist_path.exists()):
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            fields = KnowledgeItem.__dataclass_fields__
            for d in data.get("items", []):
                item = KnowledgeItem(**{k: v for k, v in d.items() if k in fields})
                if item.id in self.items:
                    continue
                self.items[item.id] = item
                self.graph.add_item(item)
            for d in data.get("reviews", []):
                try:
                    rs = ReviewState(**d)
                    self.review_states.setdefault(rs.item_id, rs)
                except Exception:
                    pass
        except Exception:
            pass

    def _ensure_hkc_loaded(self):
        """惰性从 HKC(知识真源)载入 learning_note KU —— 只成功一次。
        铁律要点:HKC 惰性初始化,__init__ 时(_hkc_api_key 尚未设)常拿不到 gs,
        故必须在后续任意读写(analyze 轮询/learn/...)里重试载入,否则迁移删文件后
        重启会「知识凭空消失」(真源仍在 hkc.db,只是没被读回)。加载后把内存里
        文件残留的条目回迁 HKC 并删本地队列文件。"""
        if self._hkc_loaded:
            return
        gs = self._graph_store()
        if gs is None:
            return
        try:
            for ku in gs.query_all(limit=5000):
                ex = getattr(ku, "extra", {}) or {}
                if ex.get("kind") != "learning_note":
                    continue
                if getattr(ku, "status", "active") == "deleted":
                    continue
                item, review = self._ku_to_item(ku)
                if item.id in self.items:
                    continue
                self.items[item.id] = item
                self.graph.add_item(item)
                if review:
                    try:
                        self.review_states.setdefault(item.id, ReviewState(**review))
                    except Exception:
                        pass
        except Exception:
            return
        self._hkc_loaded = True
        if self._persist_path and self._persist_path.exists():
            self._save()

    def _save(self):
        """把所有知识点 put 进 HKC(真源,upsert)。HKC 拿不到 → 写离线队列文件(兜底 A)。"""
        with self._lock:
            items = list(self.items.values())
        gs = self._graph_store()
        if gs is not None:
            ok = True
            for it in items:
                try:
                    gs.put(self._item_to_ku(it))
                except Exception:
                    ok = False
            if ok:
                if self._persist_path and self._persist_path.exists():
                    try:
                        self._persist_path.unlink()
                    except Exception:
                        pass
                return
        if self._persist_path:
            try:
                data = {
                    "items": [asdict(it) for it in items],
                    "reviews": [asdict(rs) for rs in self.review_states.values()],
                }
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._persist_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._persist_path)
            except Exception:
                pass

    def learn(self, content: str, topic: str = "general",
              source: str = "", tags: list | None = None) -> dict:
        """采集一个知识点 → 存进 HKC(知识真源)+ 间隔重复队列 + 图谱。
        铁律:知识内容+笔记归 HKC,不进 HMR(HMR 只装 token 上下文/工作运行记忆)。"""
        self._ensure_hkc_loaded()
        item = KnowledgeItem(content=content, topic=topic,
                             source=source, tags=tags or [])
        with self._lock:
            self.items[item.id] = item
            self.review_states[item.id] = ReviewState(item_id=item.id)
            self.graph.add_item(item)
        self._save()
        return {"item": item.to_dict(),
                "review": self.review_states[item.id].to_dict()}

    def import_csv(self, path: str) -> int:
        """从 CSV 导入知识点。列:content,topic,source,tags
        外部工具(Readwise/Obsidian/Anki MCP)接口可在此扩展。"""
        count = 0
        with open(Path(path), encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tags = [t.strip() for t in (row.get("tags") or "").split("|") if t.strip()]
                self.learn(content=row["content"],
                           topic=row.get("topic", "general"),
                           source=row.get("source", ""), tags=tags)
                count += 1
        return count

    def review(self, item_id: str, grade: int) -> dict:
        """复习一个知识点,提交回忆质量评分(0~5),更新掌握度。"""
        self._ensure_hkc_loaded()
        state = self.review_states.get(item_id)
        if not state:
            return {"error": "未知知识点"}
        self.sr.review(state, grade)
        self._save()
        return state.to_dict()

    def delete(self, item_id: str) -> bool:
        """删除一个知识点(内存 + 复习状态 + 图谱)并落盘。返回是否命中。"""
        self._ensure_hkc_loaded()
        with self._lock:
            found = item_id in self.items
            self.items.pop(item_id, None)
            self.review_states.pop(item_id, None)
            try:
                self.graph.remove_item(item_id)
            except Exception:
                pass
        gs = self._graph_store()
        if gs is not None:
            try:
                gs.delete(item_id)
            except Exception:
                pass
        if found:
            self._save()
        return found

    def add_note(self, item_id: str, text: str) -> dict:
        """给一个知识点追加一条笔记(有内容才写盘;没新增就不存)。"""
        self._ensure_hkc_loaded()
        text = (text or "").strip()
        if not text:
            return {"error": "笔记为空"}
        with self._lock:
            item = self.items.get(item_id)
            if item is None:
                return {"error": "未知知识点"}
            item.notes.append({"text": text, "ts": datetime.now().isoformat()})
            notes = list(item.notes)
        self._save()
        return {"notes": notes}

    def due_today(self) -> list:
        """今天该复习的知识点(最弱的优先)。"""
        due = self.sr.due_items(list(self.review_states.values()))
        return [{"item": self.items[s.item_id].to_dict(),
                 "review": s.to_dict()} for s in due]

    def build_connections(self, semantic_threshold: float = 0.55) -> dict:
        """自动构建知识连接(同主题 + 语义)。"""
        topic_edges = self.graph.auto_link_by_topic()
        semantic_edges = self.graph.auto_link_by_semantic(semantic_threshold)
        return {"topic_edges": topic_edges,
                "semantic_edges": semantic_edges,
                "graph": self.graph.stats()}

    def related(self, item_id: str) -> list:
        """某知识点的关联知识(连接发现)。"""
        out = []
        for nid, etype, weight, reason in self.graph.neighbors(item_id):
            if nid in self.items:
                out.append({"item": self.items[nid].to_dict(),
                            "edge_type": etype, "weight": weight, "reason": reason})
        return out

    def strategy_check(self, daily_cap: int = 20) -> list:
        """学习策略裁决:复习提醒、薄弱主题、负荷预警、连接建议。"""
        verdicts = []
        states = list(self.review_states.values())

        due = self.sr.due_items(states)
        if due:
            verdicts.append(LearningVerdict(
                rule_id="review_due", action="review_due", severity="info",
                message=f"今天有 {len(due)} 个知识点待复习",
                payload={"count": len(due)}))

        risky = self.sr.forgetting_risk(states)
        if risky:
            verdicts.append(LearningVerdict(
                rule_id="weak_alert", action="weak_topic_alert", severity="warn",
                message=f"{len(risky)} 个知识点处于高遗忘风险,建议优先复习",
                payload={"risky": risky[:5]}))

        load = self.sr.learning_load(states)
        if load["total_due"] > daily_cap:
            verdicts.append(LearningVerdict(
                rule_id="overload", action="overload_warn", severity="warn",
                message=f"未来复习量 {load['total_due']} 超过每日上限 {daily_cap},建议分批",
                payload=load))

        connected = set()
        for e in self.graph.edges:
            connected.add(e.source_id)
            connected.add(e.target_id)
        isolated = [i for i in self.items if i not in connected]
        if isolated:
            verdicts.append(LearningVerdict(
                rule_id="suggest_connect", action="suggest_connect", severity="info",
                message=f"{len(isolated)} 个知识点尚无关联,可建立连接以加深理解",
                payload={"isolated_count": len(isolated)}))
        return verdicts

    def analyze(self) -> dict:
        self._ensure_hkc_loaded()
        states = list(self.review_states.values())
        masteries = [s.mastery for s in states]
        avg_mastery = round(sum(masteries) / len(masteries), 3) if masteries else 0.0

        from collections import defaultdict
        topic_mastery = defaultdict(list)
        for iid, item in self.items.items():
            st = self.review_states.get(iid)
            if st:
                topic_mastery[item.topic].append(st.mastery)
        topics = {t: round(sum(m) / len(m), 3) for t, m in topic_mastery.items()}

        return {
            "total_items": len(self.items),
            "avg_mastery": avg_mastery,
            "topic_mastery": topics,
            "weakest_topic": (min(topics, key=lambda t: topics[t]) if topics else None),
            "due_today": len(self.sr.due_items(states)),
            "forgetting_risk": self.sr.forgetting_risk(states),
            "learning_load": self.sr.learning_load(states),
            "graph": self.graph.stats(),
            "strategy": [v.to_dict() for v in self.strategy_check()],
        }

    def status(self) -> dict:
        self._ensure_hkc_loaded()
        return {
            "items": len(self.items),
            "reviews_tracked": len(self.review_states),
            "graph": self.graph.stats(),
        }
