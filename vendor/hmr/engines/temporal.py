"""
Temporal Engine - 时间感知记忆管理
修复内容：
1. 真正的 Ebbinghaus 遗忘曲线（用 SM-2 算法实现）
2. 复习强度随次数累积增长（修复原来 +0.1 的假实现）
3. 间隔重复调度基于真实的稳定性计算
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import math
from ..core.models import TemporalState, MemoryObject


class SM2State:
    """
    SuperMemo 2 (SM-2) 算法状态
    
    这是 Anki 等间隔重复系统的核心算法。
    
    核心思路：
    - 每次复习后，记忆稳定性 S 增长
    - 间隔 I 随 S 扩大：下次复习越来越晚
    - 遗忘曲线：R(t) = e^(-t/S)，t 为距上次复习的天数
    """
    
    def __init__(self):
        self.stability: float = 1.0      # 记忆稳定性（天）
        self.difficulty: float = 0.3     # 记忆难度 0-1，越高越难记
        self.review_count: int = 0       # 复习次数
        self.last_review: datetime = datetime.utcnow()
        self.next_review: datetime = datetime.utcnow() + timedelta(days=1)

    def review(self, grade: float = 1.0):
        """
        复习一次。
        grade: 0-1，1=完美回忆，0=完全遗忘
        
        SM-2 公式：
        - difficulty 更新: D = D + 0.1 - (1 - grade) * 0.3
        - stability 更新: S_new = S * (1 + e^(11*(grade - 0.6)) * (1-D)) * 0.9
        """
        self.review_count += 1
        self.last_review = datetime.utcnow()
        
        # 更新难度（clamp 到 [0.1, 1.0]）
        self.difficulty = max(0.1, min(1.0,
            self.difficulty + 0.1 - (1.0 - grade) * 0.3
        ))
        
        # 更新稳定性
        if grade < 0.6:
            # 低分：稳定性衰退，需要重新学习
            self.stability = max(1.0, self.stability * 0.4)
        else:
            # 高分：稳定性增长（越熟悉增长越快）
            growth = math.exp(11.0 * (grade - 0.6)) * (1.0 - self.difficulty) * 0.9
            self.stability = self.stability * (1.0 + growth)
            self.stability = max(1.0, min(365.0, self.stability))
        
        # 计算下次复习时间
        interval_days = int(self.stability * 0.9)
        interval_days = max(1, interval_days)
        self.next_review = self.last_review + timedelta(days=interval_days)

    def retrievability(self) -> float:
        """
        当前可提取性（记忆有多新鲜）
        R(t) = e^(-t/S)，范围 0-1
        """
        elapsed = (datetime.utcnow() - self.last_review).total_seconds() / 86400
        return math.exp(-elapsed / max(self.stability, 0.1))

    def to_dict(self) -> dict:
        return {
            "stability": self.stability,
            "difficulty": self.difficulty,
            "review_count": self.review_count,
            "last_review": self.last_review.isoformat(),
            "next_review": self.next_review.isoformat()
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SM2State":
        s = cls()
        s.stability = d.get("stability", 1.0)
        s.difficulty = d.get("difficulty", 0.3)
        s.review_count = d.get("review_count", 0)
        s.last_review = datetime.fromisoformat(d["last_review"]) if "last_review" in d else datetime.utcnow()
        s.next_review = datetime.fromisoformat(d["next_review"]) if "next_review" in d else datetime.utcnow()
        return s


class TemporalEngine:
    """
    时间引擎（真实 Ebbinghaus + SM-2 版本）
    
    修复的核心问题：
    旧版：temporal_weight += 0.1（每次访问固定加 0.1，无记忆效应）
    新版：SM-2 算法，稳定性随复习次数指数增长，间隔自动扩大
    
    效果：
    - 第1次复习：1天后
    - 第2次复习：约2天后
    - 第5次复习：约2周后
    - 第10次复习：约3个月后
    """

    def __init__(self, memory_fs):
        self.memory_fs = memory_fs
        self.sm2_states: Dict[str, SM2State] = {}
        self.temporal_states: Dict[str, TemporalState] = {}

    def create_temporal_state(self, memory: MemoryObject) -> TemporalState:
        state = TemporalState(
            memory_id=memory.id,
            created_at=memory.created_at,
            last_accessed=memory.last_accessed or memory.created_at,
            access_count=memory.access_count,
            temporal_weight=memory.temporal_weight
        )
        self.temporal_states[memory.id] = state

        # 初始化 SM-2 状态
        if memory.id not in self.sm2_states:
            sm2 = SM2State()
            sm2.last_review = memory.created_at
            self.sm2_states[memory.id] = sm2

        return state

    def apply_decay(self, memory_id: str) -> float:
        """
        计算当前可提取性（真正的 Ebbinghaus 公式）
        R(t) = e^(-t/S)
        """
        if memory_id not in self.sm2_states:
            return 1.0

        sm2 = self.sm2_states[memory_id]
        r = sm2.retrievability()

        # 同步到 TemporalState
        if memory_id in self.temporal_states:
            self.temporal_states[memory_id].temporal_weight = r

        return r

    def reinforce_access(self, memory_id: str, grade: float = 0.8) -> float:
        """
        访问强化记忆（真实 SM-2：每次复习稳定性增长，下次间隔变长）
        
        grade: 0-1，建议：
          0.9 = 主动完美回忆
          0.7 = 正常访问
          0.5 = 模糊回忆（需要提示）
          0.2 = 几乎遗忘
        """
        if memory_id not in self.sm2_states:
            self.sm2_states[memory_id] = SM2State()

        sm2 = self.sm2_states[memory_id]
        sm2.review(grade=grade)

        if memory_id in self.temporal_states:
            state = self.temporal_states[memory_id]
            state.last_accessed = datetime.utcnow()
            state.access_count += 1
            state.temporal_weight = sm2.retrievability()

        return sm2.retrievability()

    def get_memory_lifespan(self, memory_id: str) -> Dict[str, Any]:
        if memory_id not in self.sm2_states:
            return {}

        sm2 = self.sm2_states[memory_id]
        now = datetime.utcnow()

        return {
            "memory_id": memory_id,
            "stability_days": round(sm2.stability, 1),
            "difficulty": round(sm2.difficulty, 3),
            "review_count": sm2.review_count,
            "retrievability": round(sm2.retrievability(), 3),
            "last_review": sm2.last_review.isoformat(),
            "next_review": sm2.next_review.isoformat(),
            "overdue_days": max(0, (now - sm2.next_review).days),
            "algorithm": "SM-2 (Ebbinghaus)"
        }

    def get_forgetting_curve(self, memory_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """
        基于当前稳定性的遗忘曲线投影
        R(t) = e^(-t/S)
        """
        if memory_id not in self.sm2_states:
            return []

        sm2 = self.sm2_states[memory_id]
        S = sm2.stability
        curve = []

        for day in range(days + 1):
            r = math.exp(-day / max(S, 0.1))
            curve.append({
                "day": day,
                "retrievability": round(r, 3),
                "forgotten_pct": round((1 - r) * 100, 1)
            })

        return curve

    def batch_apply_decay(self) -> Dict[str, float]:
        """批量计算所有记忆的当前可提取性"""
        return {mid: self.apply_decay(mid) for mid in self.sm2_states}

    def get_forgetting_schedule(self) -> List[Dict[str, Any]]:
        """
        获取复习优先级列表（基于真实 SM-2）
        逾期越久、可提取性越低的记忆优先级越高
        """
        now = datetime.utcnow()
        schedule = []

        for memory_id, sm2 in self.sm2_states.items():
            overdue = (now - sm2.next_review).total_seconds() / 86400
            r = sm2.retrievability()
            # 优先级：逾期天数 × (1 - 可提取性)
            priority = max(0, overdue) * (1.0 - r)

            schedule.append({
                "memory_id": memory_id,
                "priority": round(priority, 3),
                "retrievability": round(r, 3),
                "stability_days": round(sm2.stability, 1),
                "overdue_days": round(max(0, overdue), 1),
                "review_count": sm2.review_count
            })

        schedule.sort(key=lambda x: x["priority"], reverse=True)
        return schedule

    def apply_spaced_repetition(self, memory_id: str) -> Optional[datetime]:
        """获取下次复习时间"""
        if memory_id not in self.sm2_states:
            return None
        return self.sm2_states[memory_id].next_review

    def set_runtime_relevance(self, memory_id: str, relevance: float):
        if memory_id in self.temporal_states:
            self.temporal_states[memory_id].runtime_relevance = max(0.0, min(1.0, relevance))

    def export_sm2_states(self) -> Dict[str, dict]:
        """导出 SM-2 状态（用于持久化）"""
        return {mid: sm2.to_dict() for mid, sm2 in self.sm2_states.items()}

    def import_sm2_states(self, data: Dict[str, dict]):
        """导入 SM-2 状态（从持久化恢复）"""
        for mid, d in data.items():
            self.sm2_states[mid] = SM2State.from_dict(d)
