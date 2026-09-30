"""
HMR v1 - Core Data Structures
Hestia Memory Runtime
"""

from typing import List, Dict, Any, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field
from uuid import uuid4


# ============================================================================
# Memory Object - Core cognitive memory unit
# ============================================================================

class MemoryObject(BaseModel):
    """
    A Memory Object is not a document - it's a cognitive artifact.
    
    It represents:
    - A concept learned
    - A decision made
    - A runtime state
    - An execution trace
    - A project context
    """
    
    id: str = Field(default_factory=lambda: f"mem_{uuid4().hex[:8]}")
    type: Literal[
        "concept",      # Abstract knowledge
        "project",      # Project context
        "decision",     # Decision + rationale
        "runtime",      # Runtime state snapshot
        "task",         # Task + status
        "workflow",     # Process definition
        "agent_memory", # Agent-specific memory
        "execution",    # Execution trace
        "reflection"    # Post-execution learning
    ]
    
    title: str
    content: str
    semantic_summary: str
    
    # Runtime connections
    linked_memories: List[str] = Field(default_factory=list)
    runtime_dependencies: List[str] = Field(default_factory=list)
    
    # Metadata
    tags: List[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    
    # Temporal
    temporal_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    
    # Vector embedding (computed)
    embedding: Optional[List[float]] = None
    
    def reinforce(self):
        """Strengthen memory through access"""
        self.access_count += 1
        self.last_accessed = datetime.utcnow()
        # Temporal weight increases with recent access
        self.temporal_weight = min(1.0, self.temporal_weight + 0.1)
    
    def decay(self, factor: float = 0.05):
        """Natural forgetting curve"""
        self.temporal_weight = max(0.0, self.temporal_weight - factor)


# ============================================================================
# Runtime State - Cognitive state persistence
# ============================================================================

class RuntimeState(BaseModel):
    """
    RuntimeState captures the *cognitive state* of an AI system.
    
    Not just chat history - but:
    - What is it trying to do?
    - What is it thinking about?
    - What context is active?
    - What tasks are pending?
    """
    
    runtime_id: str = Field(default_factory=lambda: f"rt_{uuid4().hex[:8]}")
    
    # Active cognition
    active_goal: Optional[str] = None
    current_plan: List[str] = Field(default_factory=list)
    active_reasoning: Optional[str] = None
    
    # Active resources
    active_agents: List[str] = Field(default_factory=list)
    active_memory_ids: List[str] = Field(default_factory=list)
    
    # Context
    current_context: Dict[str, Any] = Field(default_factory=dict)
    focus_areas: List[str] = Field(default_factory=list)
    
    # Task state
    pending_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    completed_tasks: List[str] = Field(default_factory=list)
    
    # Cognitive metrics
    uncertainty_level: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_level: float = Field(default=0.5, ge=0.0, le=1.0)
    
    # Temporal
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def snapshot(self) -> Dict[str, Any]:
        """Create a snapshot for persistence"""
        return self.model_dump()
    
    @classmethod
    def restore(cls, snapshot: Dict[str, Any]) -> 'RuntimeState':
        """Restore from snapshot"""
        return cls(**snapshot)


# ============================================================================
# Agent Workspace - Per-agent working memory
# ============================================================================

class AgentWorkspace(BaseModel):
    """
    Each agent gets its own workspace - a cognitive scratchpad.
    
    Think of it like:
    - Your desk while working
    - Your active train of thought
    - Your current to-do list
    """
    
    agent_id: str
    
    # Active work
    active_goal: Optional[str] = None
    active_task: Optional[str] = None
    
    # Working memory (loaded from HMR)
    active_memory_ids: List[str] = Field(default_factory=list)
    
    # Temporary reasoning (not persisted to long-term)
    temporary_thoughts: List[str] = Field(default_factory=list)
    temporary_notes: Dict[str, str] = Field(default_factory=dict)
    
    # Task management
    task_stack: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Runtime linkage
    active_runtime_id: Optional[str] = None
    
    # Status
    status: Literal["idle", "working", "blocked", "waiting"] = "idle"
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def push_task(self, task: Dict[str, Any]):
        """Add task to stack"""
        self.task_stack.append(task)
        self.status = "working"
    
    def pop_task(self) -> Optional[Dict[str, Any]]:
        """Complete current task"""
        if self.task_stack:
            return self.task_stack.pop()
        return None
    
    def load_memory(self, memory_id: str):
        """Load memory into workspace"""
        if memory_id not in self.active_memory_ids:
            self.active_memory_ids.append(memory_id)


# ============================================================================
# Cognitive Graph Node - CWG building blocks
# ============================================================================

class CognitiveNode(BaseModel):
    """
    A node in the Cognitive Workspace Graph.
    
    Not a knowledge graph node - a *runtime dependency* node.
    """
    
    node_id: str = Field(default_factory=lambda: f"node_{uuid4().hex[:8]}")
    type: Literal[
        "goal",
        "thought",
        "decision",
        "execution",
        "reflection",
        "agent",
        "runtime",
        "memory"
    ]
    
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Graph structure
    depends_on: List[str] = Field(default_factory=list)
    enables: List[str] = Field(default_factory=list)
    
    # Status
    status: Literal["pending", "active", "completed", "failed"] = "pending"
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Memory Event - For ingestion layer
# ============================================================================

class MemoryEvent(BaseModel):
    """
    An event that should be ingested into HMR.
    
    Could come from:
    - User chat
    - Agent execution
    - Tool results
    - System events
    """
    
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:8]}")
    source: Literal["user", "agent", "tool", "system"]
    
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Context
    runtime_id: Optional[str] = None
    agent_id: Optional[str] = None
    
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Temporal State - For temporal memory
# ============================================================================

class TemporalState(BaseModel):
    """
    Temporal metadata for memory objects.
    
    Enables:
    - Recency effects
    - Forgetting curves
    - Reinforcement learning
    """
    
    memory_id: str
    
    # Access patterns
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    
    # Temporal weight (1.0 = fresh, 0.0 = forgotten)
    temporal_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    decay_factor: float = Field(default=0.05, ge=0.0, le=1.0)
    
    # Runtime relevance (changes based on current runtime)
    runtime_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    
    def update_access(self):
        """Record access"""
        self.last_accessed = datetime.utcnow()
        self.access_count += 1
        self.temporal_weight = min(1.0, self.temporal_weight + 0.1)
    
    def apply_decay(self):
        """Apply forgetting curve"""
        time_delta = datetime.utcnow() - self.last_accessed
        days = time_delta.days
        if days > 0:
            decay = self.decay_factor * days
            self.temporal_weight = max(0.0, self.temporal_weight - decay)


# ============================================================================
# Recall Result - For active recall engine
# ============================================================================

class RecallResult(BaseModel):
    """
    Result from active recall engine.
    
    Not just search results - *predicted relevant memories*.
    """
    
    memory_objects: List[MemoryObject]
    
    # Explanation
    recall_reasoning: str
    predicted_need: List[str]
    
    # Scores
    relevance_scores: Dict[str, float]
    
    timestamp: datetime = Field(default_factory=datetime.utcnow)
