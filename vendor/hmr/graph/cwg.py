"""
Cognitive Workspace Graph (CWG)
Runtime dependency tracking, not knowledge graph
"""

from typing import Dict, List, Set, Optional, Any
from datetime import datetime
from ..core.models import CognitiveNode


class CognitiveWorkspaceGraph:
    """
    Cognitive Workspace Graph
    
    NOT a knowledge graph.
    
    KG stores facts: "Scheduler depends on IPC"
    CWG stores runtime: "Current Scheduler design blocked on IPC decision"
    
    CWG tracks:
    - What is being thought about RIGHT NOW
    - What decisions block what tasks
    - What agents are working on what
    - What memories enable what actions
    """
    
    def __init__(self, memory_fs):
        self.memory_fs = memory_fs
        
        # Graph structure
        self.nodes: Dict[str, CognitiveNode] = {}
        self.edges: Dict[str, Set[str]] = {}  # node_id -> set of dependent node_ids
        self.reverse_edges: Dict[str, Set[str]] = {}  # node_id -> set of dependent node_ids
    
    def add_node(self, node: CognitiveNode) -> str:
        """
        Add node to graph.
        
        Args:
            node: CognitiveNode to add
        
        Returns:
            Node ID
        """
        self.nodes[node.node_id] = node
        self.edges[node.node_id] = set()
        self.reverse_edges[node.node_id] = set()
        
        return node.node_id
    
    def add_edge(
        self,
        from_node_id: str,
        to_node_id: str,
        relationship: str = "depends_on"
    ) -> bool:
        """
        Add edge (dependency) between nodes.
        
        Args:
            from_node_id: Source node
            to_node_id: Target node
            relationship: Type of dependency
        
        Returns:
            True if edge added
        """
        if from_node_id not in self.nodes or to_node_id not in self.nodes:
            return False
        
        # Add forward edge
        self.edges[from_node_id].add(to_node_id)
        
        # Add reverse edge
        self.reverse_edges[to_node_id].add(from_node_id)
        
        return True
    
    def link_to_runtime(
        self,
        node_id: str,
        runtime_id: str
    ) -> bool:
        """
        Link node to active runtime.
        
        Args:
            node_id: Node to link
            runtime_id: Runtime ID
        
        Returns:
            True if linked
        """
        if node_id not in self.nodes:
            return False
        
        node = self.nodes[node_id]
        node.metadata["runtime_id"] = runtime_id
        
        return True
    
    def get_dependencies(self, node_id: str) -> List[CognitiveNode]:
        """
        Get all dependencies of a node.
        
        Args:
            node_id: Node to analyze
        
        Returns:
            List of dependent nodes
        """
        if node_id not in self.nodes:
            return []
        
        dependent_ids = self.edges.get(node_id, set())
        
        return [self.nodes[dep_id] for dep_id in dependent_ids]
    
    def get_blockers(self, node_id: str) -> List[CognitiveNode]:
        """
        Get nodes that block this node.
        
        Args:
            node_id: Node to analyze
        
        Returns:
            List of blocking nodes
        """
        if node_id not in self.nodes:
            return []
        
        blocker_ids = self.reverse_edges.get(node_id, set())
        
        return [self.nodes[blocker_id] for blocker_id in blocker_ids]
    
    def get_active_thoughts(self) -> List[CognitiveNode]:
        """
        Get all active (pending or in-progress) thoughts.
        
        Returns:
            List of active nodes
        """
        return [
            node for node in self.nodes.values()
            if node.status in ["pending", "active"]
        ]
    
    def get_critical_path(self) -> List[CognitiveNode]:
        """
        Get critical path (longest dependency chain).
        
        Args:
            None
        
        Returns:
            List of nodes in critical path
        """
        if not self.nodes:
            return []
        
        # Find longest chain
        longest_path = []
        
        for node_id in self.nodes:
            path = self._find_longest_path(node_id)
            if len(path) > len(longest_path):
                longest_path = path
        
        return [self.nodes[nid] for nid in longest_path]
    
    def get_blocked_tasks(self) -> List[Dict[str, Any]]:
        """
        Get all blocked tasks and their blockers.
        
        Returns:
            List of (task, blockers) pairs
        """
        blocked = []
        
        for node in self.nodes.values():
            if node.status == "blocked":
                blockers = self.get_blockers(node.node_id)
                
                if blockers:
                    blocked.append({
                        "task": node,
                        "blockers": blockers,
                        "blocker_count": len(blockers)
                    })
        
        return blocked
    
    def resolve_blocker(self, blocker_id: str) -> List[str]:
        """
        When a blocker is resolved, update dependent tasks.
        
        Args:
            blocker_id: Blocker node ID
        
        Returns:
            List of unblocked task IDs
        """
        unblocked = []
        
        if blocker_id not in self.nodes:
            return unblocked
        
        # Get tasks blocked by this
        blocked_by_this = self.edges.get(blocker_id, set())
        
        for task_id in blocked_by_this:
            # Check if still blocked by others
            blockers = self.get_blockers(task_id)
            
            if not blockers:
                # No longer blocked
                if task_id in self.nodes:
                    self.nodes[task_id].status = "pending"
                    unblocked.append(task_id)
        
        return unblocked
    
    def mark_complete(self, node_id: str) -> List[str]:
        """
        Mark node as complete.
        
        Args:
            node_id: Node to complete
        
        Returns:
            List of newly unblocked tasks
        """
        if node_id not in self.nodes:
            return []
        
        node = self.nodes[node_id]
        node.status = "completed"
        
        # Resolve blocker
        return self.resolve_blocker(node_id)
    
    def get_graph_visualization(self) -> Dict[str, Any]:
        """
        Get graph data for visualization.
        
        Returns:
            Graph structure suitable for visualization
        """
        return {
            "nodes": [
                {
                    "id": node.node_id,
                    "type": node.type,
                    "content": node.content[:50],  # Truncate
                    "status": node.status,
                    "metadata": node.metadata
                }
                for node in self.nodes.values()
            ],
            "edges": [
                {
                    "from": node_id,
                    "to": dep_id,
                    "relationship": "depends_on"
                }
                for node_id, deps in self.edges.items()
                for dep_id in deps
            ]
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get graph statistics.
        
        Returns:
            Statistics
        """
        total_nodes = len(self.nodes)
        total_edges = sum(len(deps) for deps in self.edges.values())
        
        status_counts = {}
        for node in self.nodes.values():
            status_counts[node.status] = status_counts.get(node.status, 0) + 1
        
        type_counts = {}
        for node in self.nodes.values():
            type_counts[node.type] = type_counts.get(node.type, 0) + 1
        
        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "status_distribution": status_counts,
            "type_distribution": type_counts,
            "critical_path_length": len(self.get_critical_path()),
            "blocked_tasks": len([n for n in self.nodes.values() if n.status == "blocked"])
        }
    
    # ========================================================================
    # Helper methods
    # ========================================================================
    
    def _find_longest_path(
        self,
        node_id: str,
        visited: Optional[Set[str]] = None
    ) -> List[str]:
        """
        Find longest path from node (DFS).
        
        Args:
            node_id: Starting node
            visited: Visited nodes (for cycle detection)
        
        Returns:
            List of node IDs in path
        """
        if visited is None:
            visited = set()
        
        if node_id in visited:
            return [node_id]
        
        visited = visited | {node_id}
        
        # Get dependencies
        deps = self.edges.get(node_id, set())
        
        if not deps:
            return [node_id]
        
        # Recursively find longest path through deps
        longest = [node_id]
        
        for dep_id in deps:
            path = self._find_longest_path(dep_id, visited)
            if len(path) + 1 > len(longest):
                longest = [node_id] + path
        
        return longest
