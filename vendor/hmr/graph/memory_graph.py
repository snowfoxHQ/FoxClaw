"""
Memory Graph Layer - 记忆图层
HMR v1.5 新增

解决 CWG 的局限：CWG 只是运行时依赖图（goal/task）
Memory Graph 是结构化认知网络：实体、因果、时序、语义聚类

节点类型：
    EntityNode    → 实体（人、系统、概念、组件）
    EpisodeNode   → 情节（一段时间内的完整事件）
    ConceptNode   → 抽象概念

边类型：
    CausalEdge    → 因果关系（A 导致 B）
    TemporalEdge  → 时序关系（A 发生在 B 之前）
    SemanticEdge  → 语义相似（A 和 B 概念相近）
    PartOfEdge    → 组成关系（A 是 B 的一部分）
"""

import re
import json
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ============================================================================
# 节点和边定义
# ============================================================================

class NodeType(Enum):
    ENTITY  = "entity"    # 实体：调度器、IPC、Agent
    EPISODE = "episode"   # 情节：一次完整的失败/成功事件
    CONCEPT = "concept"   # 概念：异步、死锁、优先级队列


class EdgeType(Enum):
    CAUSAL   = "causal"    # A → B（A 导致 B）
    TEMPORAL = "temporal"  # A → B（A 在 B 之前）
    SEMANTIC = "semantic"  # A ≈ B（语义相近）
    PART_OF  = "part_of"   # A ∈ B（A 是 B 的一部分）
    RELATED  = "related"   # A ~ B（弱关联）


@dataclass
class GraphNode:
    node_id: str
    node_type: NodeType
    label: str                          # 显示名称
    memory_ids: List[str]               # 关联的 MemoryObject ID 列表
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    access_count: int = 0

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "label": self.label,
            "memory_ids": self.memory_ids,
            "attributes": self.attributes,
            "created_at": self.created_at.isoformat(),
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphNode":
        d = d.copy()
        d["node_type"] = NodeType(d["node_type"])
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        return cls(**d)


@dataclass
class GraphEdge:
    edge_id: str
    edge_type: EdgeType
    from_node: str
    to_node: str
    weight: float = 1.0
    description: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type.value,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "weight": self.weight,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphEdge":
        d = d.copy()
        d["edge_type"] = EdgeType(d["edge_type"])
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        return cls(**d)


# ============================================================================
# Entity Extractor（从记忆文本中提取实体）
# ============================================================================

class EntityExtractor:
    """
    从记忆内容中提取实体和关系。
    优先用 LLM，降级到规则提取。
    """

    def extract(self, memory_content: str, memory_title: str) -> Dict[str, Any]:
        """
        返回：
        {
            "entities": [{"label": "调度器", "type": "entity"}, ...],
            "relations": [{"from": "死锁", "to": "调度器", "type": "causal"}, ...]
        }
        """
        llm_result = self._extract_with_llm(memory_content, memory_title)
        if llm_result:
            return llm_result
        return self._extract_with_rules(memory_content, memory_title)

    def _extract_with_llm(self, content: str, title: str) -> Optional[Dict]:
        try:
            import openai, os
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key:
                return None
            client = openai.OpenAI(api_key=key)
            prompt = f"""从以下记忆中提取实体和关系，返回 JSON：
标题：{title}
内容：{content[:500]}

返回格式（只返回 JSON）：
{{
  "entities": [{{"label": "实体名", "type": "entity|concept"}}],
  "relations": [{{"from": "实体A", "to": "实体B", "type": "causal|temporal|semantic|part_of"}}]
}}"""
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300, temperature=0
            )
            return json.loads(resp.choices[0].message.content.strip())
        except Exception:
            return None

    def _extract_with_rules(self, content: str, title: str) -> Dict[str, Any]:
        """规则提取：技术实体 + 因果关键词"""
        text = f"{title} {content}"

        # 提取技术实体（大写开头的英文词 + 中文技术名词）
        en_entities = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b', text)
        zh_entities = re.findall(r'[\u4e00-\u9fa5]{2,6}(?:器|机|层|库|引擎|系统|协议|算法|模型|服务)', text)
        all_entities = list(set(en_entities + zh_entities))[:8]

        entities = [
            {"label": e, "type": "entity" if re.match(r'[A-Z]', e) else "concept"}
            for e in all_entities
        ]

        # 因果关系提取
        relations = []
        causal_patterns = [
            (r'(.{2,8})导致(.{2,8})', "causal"),
            (r'(.{2,8})引起(.{2,8})', "causal"),
            (r'(.{2,8})依赖(.{2,8})', "part_of"),
            (r'(.{2,8})是(.{2,8})的一部分', "part_of"),
        ]
        for pattern, rel_type in causal_patterns:
            for match in re.finditer(pattern, text):
                relations.append({
                    "from": match.group(1).strip(),
                    "to": match.group(2).strip(),
                    "type": rel_type
                })

        return {"entities": entities, "relations": relations}


# ============================================================================
# Semantic Cluster（语义聚类）
# ============================================================================

@dataclass
class SemanticCluster:
    """语义聚类：相似记忆自动归组"""
    cluster_id: str
    label: str
    node_ids: List[str]      # 属于这个聚类的节点
    centroid_embedding: Optional[List[float]] = None  # 聚类中心向量

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "node_ids": self.node_ids,
            "centroid_embedding": self.centroid_embedding
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticCluster":
        return cls(**d)


# ============================================================================
# Memory Graph（主类）
# ============================================================================

class MemoryGraph:
    """
    结构化认知记忆图

    与 CWG 的分工：
        CWG → 运行时依赖（goal/task 层）
        MemoryGraph → 知识结构（entity/episode/concept 层）

    使用：
        graph = MemoryGraph(storage_path)

        # 从记忆中提取并加入图
        graph.add_from_memory(memory_object)

        # 查询：找与"调度器"相关的所有节点
        nodes = graph.find_related("调度器", depth=2)

        # 查询：找"死锁"的因果链
        chain = graph.get_causal_chain("死锁")
    """

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.Lock()

        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[str, GraphEdge] = {}
        self.clusters: Dict[str, SemanticCluster] = {}

        # 快速查找：label → node_id
        self._label_index: Dict[str, str] = {}
        # 快速查找：node_id → [edge_id]
        self._adjacency: Dict[str, List[str]] = {}

        self._extractor = EntityExtractor()
        self._edge_counter = 0

        if self.storage_path:
            self._load()

    # -------------------------------------------------------------------------
    # 从记忆构建图
    # -------------------------------------------------------------------------

    def add_from_memory(self, memory) -> List[str]:
        """
        从 MemoryObject 提取实体/关系，加入图。
        返回新增的 node_id 列表。
        """
        extracted = self._extractor.extract(memory.content, memory.title)
        added_ids = []

        with self._lock:
            # 加入实体节点
            entity_node_map: Dict[str, str] = {}  # label → node_id
            for ent in extracted.get("entities", []):
                label = ent["label"]
                ntype = NodeType.ENTITY if ent["type"] == "entity" else NodeType.CONCEPT

                node_id = self._label_index.get(label)
                if node_id:
                    # 节点已存在，关联新记忆
                    if memory.id not in self.nodes[node_id].memory_ids:
                        self.nodes[node_id].memory_ids.append(memory.id)
                else:
                    # 新节点
                    node_id = f"ng_{len(self.nodes):04d}_{label[:8]}"
                    node = GraphNode(
                        node_id=node_id,
                        node_type=ntype,
                        label=label,
                        memory_ids=[memory.id]
                    )
                    self.nodes[node_id] = node
                    self._label_index[label] = node_id
                    self._adjacency[node_id] = []
                    added_ids.append(node_id)

                entity_node_map[label] = node_id

            # 加入情节节点（每条 execution/reflection 记忆是一个情节）
            if memory.type in ("execution", "reflection"):
                ep_id = f"ep_{memory.id[:8]}"
                if ep_id not in self.nodes:
                    episode = GraphNode(
                        node_id=ep_id,
                        node_type=NodeType.EPISODE,
                        label=memory.title[:30],
                        memory_ids=[memory.id],
                        attributes={"memory_type": memory.type, "confidence": memory.confidence}
                    )
                    self.nodes[ep_id] = episode
                    self._adjacency[ep_id] = []
                    added_ids.append(ep_id)

                    # 情节与相关实体建立边
                    for label, nid in entity_node_map.items():
                        self._add_edge_internal(ep_id, nid, EdgeType.RELATED, 0.5, f"情节包含实体{label}")

            # 加入关系边
            for rel in extracted.get("relations", []):
                from_id = entity_node_map.get(rel["from"])
                to_id = entity_node_map.get(rel["to"])
                if from_id and to_id and from_id != to_id:
                    etype = {
                        "causal": EdgeType.CAUSAL,
                        "temporal": EdgeType.TEMPORAL,
                        "semantic": EdgeType.SEMANTIC,
                        "part_of": EdgeType.PART_OF,
                    }.get(rel["type"], EdgeType.RELATED)
                    self._add_edge_internal(from_id, to_id, etype, 1.0, rel.get("description", ""))

        if self.storage_path and added_ids:
            self._save()

        return added_ids

    def _add_edge_internal(
        self, from_id: str, to_id: str,
        etype: EdgeType, weight: float, desc: str
    ):
        """内部加边（调用前须持锁）"""
        self._edge_counter += 1
        edge_id = f"e_{self._edge_counter:05d}"
        edge = GraphEdge(
            edge_id=edge_id, edge_type=etype,
            from_node=from_id, to_node=to_id,
            weight=weight, description=desc
        )
        self.edges[edge_id] = edge
        if from_id in self._adjacency:
            self._adjacency[from_id].append(edge_id)

    # -------------------------------------------------------------------------
    # 图查询
    # -------------------------------------------------------------------------

    def find_related(self, label: str, depth: int = 2) -> List[GraphNode]:
        """
        从指定实体出发，沿边展开 depth 层，返回相关节点。
        用于召回增强：找到相关实体后，再用其 memory_ids 加载记忆。
        """
        start_id = self._label_index.get(label)
        if not start_id:
            # 模糊匹配
            for lbl, nid in self._label_index.items():
                if label.lower() in lbl.lower() or lbl.lower() in label.lower():
                    start_id = nid
                    break
        if not start_id:
            return []

        visited: Set[str] = {start_id}
        frontier = [start_id]
        result_nodes = [self.nodes[start_id]]

        for _ in range(depth):
            next_frontier = []
            for nid in frontier:
                for edge_id in self._adjacency.get(nid, []):
                    edge = self.edges[edge_id]
                    neighbor = edge.to_node
                    if neighbor not in visited and neighbor in self.nodes:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
                        result_nodes.append(self.nodes[neighbor])
            frontier = next_frontier
            if not frontier:
                break

        return result_nodes

    def get_causal_chain(self, label: str) -> List[Tuple[GraphNode, GraphEdge]]:
        """
        获取以某实体为起点的因果链。
        例："死锁" → [死锁 →(causal)→ 超时 →(causal)→ 服务崩溃]
        """
        start_id = self._label_index.get(label)
        if not start_id:
            return []

        chain = []
        current = start_id
        visited = {current}

        for _ in range(10):  # 最长 10 跳
            causal_edges = [
                self.edges[eid]
                for eid in self._adjacency.get(current, [])
                if self.edges[eid].edge_type == EdgeType.CAUSAL
                and self.edges[eid].to_node not in visited
            ]
            if not causal_edges:
                break
            best_edge = max(causal_edges, key=lambda e: e.weight)
            next_node = best_edge.to_node
            if next_node not in self.nodes:
                break
            chain.append((self.nodes[next_node], best_edge))
            visited.add(next_node)
            current = next_node

        return chain

    def get_memory_ids_for_query(self, query: str) -> List[str]:
        """
        图路径检索：把 query 转为相关实体，再找记忆 ID。
        供 Memory Scheduler 的 GRAPH 策略使用。
        """
        # 提取 query 里的实体
        words = re.findall(r'[A-Z][a-zA-Z]{2,}|[\u4e00-\u9fa5]{2,}', query)
        memory_ids = []
        seen = set()

        for word in words[:5]:
            related_nodes = self.find_related(word, depth=2)
            for node in related_nodes:
                node.access_count += 1
                for mid in node.memory_ids:
                    if mid not in seen:
                        seen.add(mid)
                        memory_ids.append(mid)

        return memory_ids[:20]

    def auto_cluster(self, vector_store=None) -> List[SemanticCluster]:
        """
        自动语义聚类：将相似节点归组。
        简化版：按标签字符串相似度聚类（生产环境用向量聚类）。
        """
        entity_nodes = [n for n in self.nodes.values() if n.node_type == NodeType.ENTITY]
        if len(entity_nodes) < 3:
            return []

        # 简单：按标签前缀/后缀分组
        groups: Dict[str, List[str]] = {}
        for node in entity_nodes:
            key = node.label[:4]  # 用前4字符作为粗粒度分组键
            groups.setdefault(key, []).append(node.node_id)

        clusters = []
        for key, node_ids in groups.items():
            if len(node_ids) < 2:
                continue
            cid = f"cluster_{key}"
            cluster = SemanticCluster(
                cluster_id=cid,
                label=f"「{key}」相关",
                node_ids=node_ids
            )
            self.clusters[cid] = cluster
            clusters.append(cluster)

        if self.storage_path:
            self._save()

        return clusters

    def get_stats(self) -> Dict[str, Any]:
        node_types = {}
        for n in self.nodes.values():
            node_types[n.node_type.value] = node_types.get(n.node_type.value, 0) + 1
        edge_types = {}
        for e in self.edges.values():
            edge_types[e.edge_type.value] = edge_types.get(e.edge_type.value, 0) + 1
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "total_clusters": len(self.clusters),
            "node_types": node_types,
            "edge_types": edge_types,
            "entities_indexed": len(self._label_index),
        }

    # -------------------------------------------------------------------------
    # 持久化
    # -------------------------------------------------------------------------

    def _save(self):
        if not self.storage_path:
            return
        self.storage_path.mkdir(parents=True, exist_ok=True)

        data = {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": {eid: e.to_dict() for eid, e in self.edges.items()},
            "clusters": {cid: c.to_dict() for cid, c in self.clusters.items()},
            "label_index": self._label_index,
            "adjacency": self._adjacency,
            "edge_counter": self._edge_counter,
        }
        tmp = self.storage_path / "memory_graph.tmp"
        target = self.storage_path / "memory_graph.json"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(target)

    def _load(self):
        if not self.storage_path:
            return
        target = self.storage_path / "memory_graph.json"
        if not target.exists():
            return
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.nodes = {nid: GraphNode.from_dict(d) for nid, d in data.get("nodes", {}).items()}
            self.edges = {eid: GraphEdge.from_dict(d) for eid, d in data.get("edges", {}).items()}
            self.clusters = {cid: SemanticCluster.from_dict(d) for cid, d in data.get("clusters", {}).items()}
            self._label_index = data.get("label_index", {})
            self._adjacency = data.get("adjacency", {})
            self._edge_counter = data.get("edge_counter", 0)
            print(f"[HMR MemoryGraph] 加载: {len(self.nodes)} 节点, {len(self.edges)} 边")
        except Exception as e:
            print(f"[HMR MemoryGraph] 加载失败: {e}")
