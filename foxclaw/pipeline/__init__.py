"""
foxclaw.pipeline —— 认知管线(Cognitive Pipeline, v2 ②)。

把任务处理从"隐式直线"升级为**显式、分阶段**的认知流:
    perceive → recall → plan → route → act → verify → reflect → persist
每个 stage 是一等公民,操作共享的 PipelineContext,可插桩/替换(规则版⇄LLM版)/重放。

Step 1(抽壳):先落 recall / act 两个 stage,把 P1-1 的统一模板 run_task 委托给管线,
行为逐字不变;后续步骤往 stages 列表里加 verify/reflect/plan/route/perceive。
"""
from .cognitive_pipeline import (
    PipelineContext, CognitivePipeline,
    stage_perceive, stage_recall, stage_plan, stage_route, stage_act,
    stage_verify, stage_reflect, DEFAULT_STAGES,
)
from .supervisor_stages import SUPERVISOR_STAGES

__all__ = [
    "PipelineContext", "CognitivePipeline",
    "stage_perceive", "stage_recall", "stage_plan", "stage_route", "stage_act",
    "stage_verify", "stage_reflect", "DEFAULT_STAGES",
    "SUPERVISOR_STAGES",
]
