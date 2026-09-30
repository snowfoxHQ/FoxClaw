"""
Runtime State Engine
Manages cognitive state persistence and restoration
"""

from typing import Optional, List
from datetime import datetime
from ..core.models import RuntimeState


class RuntimeStateEngine:
    """
    Runtime State Engine
    
    This is the CORE of HMR's innovation.
    
    Not just saving chat history - saving:
    - What the AI is trying to do
    - What it's currently thinking
    - What tasks are pending
    - What context is loaded
    
    Enables:
    --------
    Session 1:
        AI is designing Scheduler
        Saves runtime state
    
    Session 2 (days later):
        Restores runtime state
        AI continues: "I was designing the Scheduler..."
        Remembers: goals, plan, context, uncertainties
    """
    
    def __init__(self, memory_fs):
        self.memory_fs = memory_fs
        
        # Active runtimes (in-memory)
        self.active_runtimes: List[RuntimeState] = []
    
    def save_state(self, state: RuntimeState) -> str:
        """
        Persist runtime state.
        
        Args:
            state: RuntimeState to save
        
        Returns:
            runtime_id
        """
        # Update timestamp
        state.updated_at = datetime.utcnow()
        
        # Save to MemoryFS
        self.memory_fs.write_runtime_state(state)
        
        # Track active
        if state not in self.active_runtimes:
            self.active_runtimes.append(state)
        
        return state.runtime_id
    
    def restore_state(
        self,
        runtime_id: Optional[str] = None
    ) -> Optional[RuntimeState]:
        """
        Restore a runtime state.
        
        Args:
            runtime_id: Specific runtime to restore (latest if None)
        
        Returns:
            Restored RuntimeState or None
        """
        if runtime_id:
            # Restore specific runtime
            state = self.memory_fs.read_runtime_state(runtime_id)
        else:
            # Get latest runtime
            all_runtimes = self.memory_fs.list_runtime_states()
            
            if not all_runtimes:
                return None
            
            # Sort by updated_at
            all_runtimes.sort(
                key=lambda s: s.updated_at,
                reverse=True
            )
            
            state = all_runtimes[0]
        
        return state
    
    def get_active_runtimes(self) -> List[RuntimeState]:
        """
        Get all active runtime states.
        
        Returns:
            List of active RuntimeStates
        """
        return self.active_runtimes
    
    def create_snapshot(self, state: RuntimeState) -> dict:
        """
        Create a serializable snapshot.
        
        Args:
            state: RuntimeState to snapshot
        
        Returns:
            Snapshot dict
        """
        return state.snapshot()
    
    def restore_from_snapshot(self, snapshot: dict) -> RuntimeState:
        """
        Restore from snapshot.
        
        Args:
            snapshot: Previously created snapshot
        
        Returns:
            Restored RuntimeState
        """
        return RuntimeState.restore(snapshot)
    
    def update_state(
        self,
        runtime_id: str,
        updates: dict
    ) -> RuntimeState:
        """
        Update existing runtime state.
        
        Args:
            runtime_id: Runtime to update
            updates: Fields to update
        
        Returns:
            Updated RuntimeState
        """
        # Load current state
        state = self.restore_state(runtime_id)
        
        if not state:
            raise ValueError(f"Runtime {runtime_id} not found")
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(state, key):
                setattr(state, key, value)
        
        # Save updated state
        self.save_state(state)
        
        return state
