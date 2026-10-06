"""OfficeAgent —— 办公（学习、知识管理）。"""

import json

from ..base import CognitiveAgent
from ._helpers import _fmt_mem

class OfficeAgent(CognitiveAgent):
    AGENT_ID = "office"
    AGENT_NAME = "办公 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "network"}

    @staticmethod
    def _strip_knowledge_json(text: str) -> str:
        """从回复中剥离 {"new_knowledge": [...]} 结构化块,只保留自然语言。"""
        if not text or "new_knowledge" not in text:
            return text
        import re
        # 去掉 {"new_knowledge": [ ... ]} 整块(含可能的 markdown 代码围栏)
        cleaned = re.sub(r'`{0,3}\s*\{\s*"new_knowledge".*?\}\s*\]\s*\}\s*`{0,3}',
                         '', text, flags=re.DOTALL)
        return cleaned.strip()

    def attach_runtime(self, runtime):
        """挂载学习内核(LearningRuntime):间隔重复 + 知识图谱。"""
        self.learn_rt = runtime

    # ── 编排协作(P3:学习领域态势,从编排层下沉至此)──────
    def situation(self) -> dict:
        rt = getattr(self, "learn_rt", None)
        if rt is None:
            return {"domain": "learning", "status": {}, "alerts": []}
        try:
            states = list(rt.review_states.values())
            due = rt.sr.due_items(states)
            risky = rt.sr.forgetting_risk(states)
        except Exception:
            due = risky = []
        status = {"total_items": len(rt.items), "due_today": len(due), "at_risk": len(risky)}
        alerts = ([{"domain": "learning", "severity": "info",
                    "msg": f"{len(risky)} 个知识点高遗忘风险待复习"}] if risky else [])
        return {"domain": "learning", "status": status, "alerts": alerts}

    def analyze_learning(self) -> dict:
        """调用学习内核做综合分析,关键洞察写入 ThoughtChain。"""
        rt = getattr(self, "learn_rt", None)
        if rt is None:
            return {"error": "学习内核未挂载"}
        report = rt.analyze()
        # 给前端「知识库」轮播提供知识条目明细:analyze 本身只有统计、无明细,
        # 否则前端读 analysis.items 拿不到 → 一直显示"知识库为空"(即便已录入)。
        try:
            with rt._lock:
                _vals = list(rt.items.values())
            report["items"] = [
                {"title": (it.content or "")[:40], "content": it.content,
                 "topic": it.topic, "tag": it.topic, "source": it.source,
                 "tags": it.tags, "id": it.id, "notes": getattr(it, "notes", [])}
                for it in reversed(_vals)
            ][:30]
        except Exception:
            report.setdefault("items", [])
        self._think(
            content=f"学习分析:{report['total_items']} 个知识点,"
                    f"平均掌握度 {report['avg_mastery']},"
                    f"{report['due_today']} 个待复习,"
                    f"图谱 {report['graph']['clusters']} 个知识簇",
            node_type="reflection", conf=0.9,
        )
        return report

    @property
    def _role_prompt(self) -> str:
        return """你是 Hestia OS 的办公 Agent，专注于学习和知识管理。

你的核心职责：
1. 帮助用户学习新知识和技能
2. 管理知识储备，建立个人知识库
3. 处理文档、报告和写作任务
4. 跟踪学习进度和知识演化

Hermes 自学原则（像赫尔墨斯那样）：
- 不是学习更多技能，而是通过每次交互变得更"智能"
- 记住用户的学习风格和偏好
- 识别知识缺口，主动补充
- 建立知识间的连接，而不只是存储孤立事实
- 每次对话都让知识库更丰富、更有组织

你的知识是活的，会生长的。"""

    _task_entities = lambda self, task: ["学习", "知识", "文档"]
    _recall_topk = 8   # 办公 Agent 广泛召回

    async def _execute(self, task, context, session_id, memories):
            self._think(
                content=f"知识任务：{task}，已有相关记忆：{len(memories)} 条",
                node_type="reasoning",
                conf=0.92,
            )

            knowledge_context = ""
            if memories:
                knowledge_context = "相关知识储备：\n" + "\n".join([
                    f"- {_fmt_mem(m, 80)}" for m in memories[:6]
                ])

            # 接入学习内核:若已挂载且有知识点,做真实分析(待复习/掌握度)作为依据
            kernel_insight = ""
            learn_rt = getattr(self, "learn_rt", None)
            if learn_rt is not None and getattr(learn_rt, "items", None):
                try:
                    report = learn_rt.analyze()
                    due = report.get("due_today", 0)
                    mastery = report.get("avg_mastery", 0)
                    kernel_insight = (
                        f"\n\n【学习状态(基于真实知识库)】\n"
                        f"· 知识点总数: {report.get('total_items', 0)}\n"
                        f"· 平均掌握度: {mastery}\n"
                        f"· 今日待复习: {due} 个")
                    self._think(
                        content=f"学习内核:{report.get('total_items',0)} 知识点,{due} 待复习",
                        node_type="reflection", conf=0.9,
                    )
                except Exception:
                    pass   # 内核失败不阻断,降级纯 LLM

            response = await self._call_llm_web(task, [{
                "role": "user",
                "content": f"""学习任务：{task}

{knowledge_context}{kernel_insight}

请：
1. 帮助完成这个学习/办公任务
2. 识别涉及的关键知识点
3. 指出哪些是新知识（需要记录）

回答后，以 JSON 格式附上需要记录的新知识：
{{"new_knowledge": [{{"title": "知识标题", "content": "知识内容", "tags": ["标签"]}}]}}"""
            }])

            # 提取并存储新知识(括号平衡解析:正确处理内容含 []{} 的情况)
            new_knowledge_count = 0
            try:
                start = response.find('{"new_knowledge"')
                if start < 0:
                    start = response.find('{ "new_knowledge"')
                if start >= 0:
                    # 从 start 起做花括号配对,找到完整 JSON 对象的结束位置
                    depth, end, in_str, esc = 0, -1, False, False
                    for i in range(start, len(response)):
                        c = response[i]
                        if esc:
                            esc = False; continue
                        if c == '\\':
                            esc = True; continue
                        if c == '"':
                            in_str = not in_str
                        elif not in_str:
                            if c == '{':
                                depth += 1
                            elif c == '}':
                                depth -= 1
                                if depth == 0:
                                    end = i + 1; break
                    if end > start:
                        knowledge_data = json.loads(response[start:end])
                        for k in knowledge_data.get("new_knowledge", []):
                            if not isinstance(k, dict) or not k.get("content"):
                                continue
                            self._store_memory(
                                content=k["content"],
                                mem_type="concept",
                                title=k.get("title", "未命名知识"),
                                tags=k.get("tags", ["office", "learning"]),
                            )
                            new_knowledge_count += 1
            except Exception:
                pass  # 解析失败不影响主流程

            # 从返回文本中剥离 new_knowledge JSON 块(不展示给用户)
            response = self._strip_knowledge_json(response)

            self._think(
                content=f"学习完成，新增知识 {new_knowledge_count} 条",
                node_type="reflection",
                conf=0.90,
            )
            return response, {"new_knowledge_stored": new_knowledge_count}

