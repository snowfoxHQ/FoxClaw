"""
Semantic Memory Engine
Handles embedding, indexing, and retrieval
"""

from typing import List, Optional, Dict, Any
import numpy as np
from ..core.models import MemoryObject


class SemanticMemoryEngine:
    """
    Semantic Memory Engine
    
    Responsibilities:
    - Generate embeddings
    - Index memories
    - Semantic retrieval
    - Hybrid search (semantic + keyword)
    """
    
    def __init__(self, vector_store, memory_fs):
        self.vector_store = vector_store
        self.memory_fs = memory_fs
        
        # Cache
        self.memory_cache: Dict[str, MemoryObject] = {}
    
    def store(self, memory: MemoryObject) -> str:
        """
        Store memory with semantic indexing.
        
        Args:
            memory: MemoryObject to store
        
        Returns:
            memory_id
        """
        # Generate embedding if not present
        if memory.embedding is None:
            memory.embedding = self.vector_store.embed(
                memory.semantic_summary
            )
        
        # Store in vector index
        self.vector_store.add(
            id=memory.id,
            embedding=memory.embedding,
            metadata={
                "type": memory.type,
                "title": memory.title,
                "tags": memory.tags,
                "temporal_weight": memory.temporal_weight
            }
        )
        
        # Store in MemoryFS
        self.memory_fs.write_memory(memory)
        
        # Cache
        self.memory_cache[memory.id] = memory
        
        return memory.id
    
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[MemoryObject]:
        """
        Semantic retrieval.
        
        Args:
            query: Search query
            top_k: Number of results
            filters: Optional filters (type, tags, etc.)
        
        Returns:
            List of relevant MemoryObjects
        """
        # Generate query embedding
        query_embedding = self.vector_store.embed(query)
        
        # Search vector store
        results = self.vector_store.search(
            query_embedding,
            top_k=top_k,
            filters=filters
        )
        
        # Load full memory objects
        memories = []
        for result in results:
            memory_id = result["id"]
            
            # Try cache first
            if memory_id in self.memory_cache:
                memory = self.memory_cache[memory_id]
            else:
                # Load from MemoryFS
                memory = self.memory_fs.read_memory(memory_id)
                self.memory_cache[memory_id] = memory
            
            # Reinforce accessed memory
            memory.reinforce()
            
            memories.append(memory)
        
        return memories
    
    def hybrid_search(
        self,
        query: str,
        keywords: List[str],
        top_k: int = 5
    ) -> List[MemoryObject]:
        """
        Hybrid search: semantic + keyword.
        
        Args:
            query: Semantic query
            keywords: Keyword filters
            top_k: Number of results
        
        Returns:
            Hybrid ranked results
        """
        # Semantic search
        semantic_results = self.retrieve(query, top_k=top_k * 2)
        
        # Keyword filtering
        filtered = []
        for memory in semantic_results:
            # Check if any keyword matches
            content_lower = memory.content.lower()
            title_lower = memory.title.lower()
            
            match_score = sum(
                1 for kw in keywords
                if kw.lower() in content_lower or kw.lower() in title_lower
            )
            
            if match_score > 0:
                filtered.append((memory, match_score))
        
        # Sort by keyword match score
        filtered.sort(key=lambda x: x[1], reverse=True)
        
        # Return top_k
        return [mem for mem, _ in filtered[:top_k]]
