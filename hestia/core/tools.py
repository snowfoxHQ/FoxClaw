"""
Hestia OS — 工具注册表(ToolRegistry / P2 function-calling)

病根(见 docs/internal/TOOL_USE_PLAN.md §0):系统里散落多套"用关键词猜用户意图"的
启发式(needs_search / _model_augment / _skill_augment …),各管一摊、彼此不知情,
加一处碰坏一处。根因是 LLM 从没机会"自己决定用哪个工具"。

本模块给出**单一意图决策源**的地基:把能力声明成 function-calling 工具(JSON Schema),
交给模型自己选。ToolRegistry 只持有**工具声明(schema)**;工具的**执行**由 Agent 侧
`_dispatch_tool` 绑定(recall_memory 需要 agent 的 HMR、web_search 走 core.web_search),
这样注册表保持无状态、可跨 Agent 复用。

模型无关:schema 用 OpenAI tools 通用格式;ModelRouter.call_tools 负责按 provider 适配/降级。
安全:执行前经 per-tool deny(env HESTIA_TOOL_DENY,逗号分隔)+ 可接 SkillPolicy 闸。
"""
from __future__ import annotations
import os


def _tool(name: str, description: str, properties: dict, required: list) -> dict:
    """构造一个 OpenAI-format function tool 声明。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


# ── 首批工具声明(高价值、正好替掉现有关键词坑)──────────────────────────────
WEB_SEARCH = _tool(
    "web_search",
    "联网搜索实时/时效性的**外部**信息:新闻、产品的版本与型号、发布动态、价格、汇率、"
    "股价、天气、排行等。仅当答案依赖当前外部世界、且不在本地时才调用;闲聊、常识、"
    "以及用户自己的账本/行程/学习/记忆等**本地数据**不要调用本工具。",
    {"query": {"type": "string", "description": "要检索的关键词或问题(尽量具体)"}},
    ["query"],
)

RECALL_MEMORY = _tool(
    "recall_memory",
    "从用户的个人记忆(HMR)里语义召回既往信息:过去的对话、偏好、事实、结论等。"
    "当回答依赖'用户之前说过/记录过什么'时调用;通用常识不需要调用。",
    {"query": {"type": "string", "description": "召回用的语义查询"}},
    ["query"],
)


class ToolRegistry:
    """工具声明注册表(无状态,只持 schema)。执行由 Agent 侧按 name 分派。"""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def register(self, schema: dict) -> "ToolRegistry":
        name = schema["function"]["name"]
        self._tools[name] = schema
        return self

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict]:
        """返回放行的工具 schema 列表(经 env HESTIA_TOOL_DENY 过滤)。"""
        deny = _denied()
        return [s for n, s in self._tools.items() if n not in deny]

    def has(self, name: str) -> bool:
        return name in self._tools


def _denied() -> set[str]:
    """被禁用的工具名集合(env HESTIA_TOOL_DENY,逗号分隔)。安全 kill-switch。"""
    raw = os.environ.get("HESTIA_TOOL_DENY", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def is_tool_denied(name: str) -> bool:
    return name in _denied()


def default_registry() -> ToolRegistry:
    """首批工具:web_search + recall_memory。后续 skills/MCP 可继续 register 进来(P3)。"""
    return ToolRegistry().register(WEB_SEARCH).register(RECALL_MEMORY)


# ── 工具能力分级(供 permission.evaluate 四阶段管线判定)────────────────────────
# 病根提醒:prompt 级隔离挡不住弱模型被块内强指令诱导(实测 flash ~2/3 被劫持),真正的
# 安全阀在**动作级**——工具执行前过权限管线,写/副作用/不可逆动作走人在回路(HITL)。
# 字段对齐 permission.evaluate:side_effect(read/write/send/pay)/ read_only / external / destructive。
# **新增任何工具都应在此声明能力;未声明者 fail-closed 当作有副作用 → 强制 HITL(见 _DEFAULT_CAP)。**
TOOL_CAPABILITIES: dict[str, dict] = {
    "web_search":    {"side_effect": "read", "read_only": True, "external": False, "destructive": False},
    "recall_memory": {"side_effect": "read", "read_only": True, "external": False, "destructive": False},
}
# 未声明工具的兜底:当有副作用 + 外部 → permission 管线判 ask → 强制人在回路。宁误拦不漏放。
_DEFAULT_CAP = {"side_effect": "write", "read_only": False, "external": True, "destructive": False}


def tool_capability(name: str) -> dict:
    """取工具能力分级;未声明 → fail-closed 兜底(有副作用,须 HITL)。"""
    return dict(TOOL_CAPABILITIES.get(name, _DEFAULT_CAP))


def tool_use_enabled() -> bool:
    """是否启用 function-calling 主路径。默认关(behavior-preserving / 可逆):
    HESTIA_TOOL_USE=1 开启后,支持 tools 的 provider 走 LLM 自主工具决策;
    不支持的 provider 或关闭时,自动回退现有关键词增强链(fallback,不破坏)。"""
    return os.environ.get("HESTIA_TOOL_USE", "0").strip() in ("1", "true", "True", "yes", "on")
