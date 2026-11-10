"""OfficeAgent —— 办公学习(学习/知识/文档/写作 + 日程/行程)。

Agent 拓扑收缩(5 → 3)后,原 `AdminAgent`(行政:日程/行程/会议)整块并入本 Agent:
  · 行程内核 ItineraryRuntime 改由 `attach_itinerary` 挂载(与学习内核并列);
  · `plan_itinerary / handle_cross_request / concede` 随之下沉到这里 —— 领域逻辑仍归
    "离内核最近的那个 Agent",编排层不直接读 itinerary/learning 内核;
  · `situation()` 改成一次自报**多个**领域(learning + itinerary),驾驶舱按 domains 聚合。

原行政 Agent 的角色行为(日程安排话术、时间偏好、冲突前置检查)已下放为指令技能
`skills/schedule-itinerary/SKILL.md`,由本 Agent 在命中触发词时自动注入 —— 不再需要一个
独立的行政 Agent 常驻。
"""

import json
from datetime import datetime

from ..base import CognitiveAgent
from ._helpers import _fmt_mem


class OfficeAgent(CognitiveAgent):
    AGENT_ID = "office"
    AGENT_NAME = "办公学习 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "network", "calendar"}

    SCHEDULE_HINTS = ("日程", "行程", "会议", "出差", "预约", "日历", "路线", "几点", "提醒")

    def attach_runtime(self, runtime):
        """挂载学习内核(LearningRuntime):间隔重复 + 知识图谱。"""
        self.learn_rt = runtime

    def attach_itinerary(self, runtime):
        """挂载行程内核(ItineraryRuntime):路线优化 + 可达性 + 守门人。

        (原 AdminAgent.attach_runtime 的职责,随行政并入办公)
        """
        self.itin = runtime

    def situation(self) -> dict:
        """一次自报两个领域:学习 + 行程。编排层按 `domains` 聚合(缺一个不影响另一个)。"""
        return {"domains": {"learning": self._learning_status(),
                            "itinerary": self._itinerary_status()},
                "alerts": self._learning_alerts() + self._itinerary_alerts()}

    def _learning_status(self) -> dict:
        rt = getattr(self, "learn_rt", None)
        if rt is None:
            return {}
        try:
            states = list(rt.review_states.values())
            due = rt.sr.due_items(states)
            risky = rt.sr.forgetting_risk(states)
        except Exception:
            due = risky = []
        return {"total_items": len(rt.items), "due_today": len(due), "at_risk": len(risky)}

    def _learning_alerts(self) -> list:
        st = self._learning_status()
        return ([{"domain": "learning", "severity": "info",
                  "msg": f"{st['at_risk']} 个知识点高遗忘风险待复习"}] if st.get("at_risk") else [])

    def _itinerary_status(self) -> dict:
        rt = getattr(self, "itin", None)
        if rt is None:
            return {}
        try:
            conflicts = rt.reasoning.detect_conflicts(rt.waypoints)
        except Exception:
            conflicts = []
        return {"waypoints": len(rt.waypoints), "conflicts": len(conflicts)}

    def _itinerary_alerts(self) -> list:
        rt = getattr(self, "itin", None)
        if rt is None:
            return []
        try:
            conflicts = rt.reasoning.detect_conflicts(rt.waypoints)
        except Exception:
            conflicts = []
        return [{"domain": "itinerary", "severity": "critical", "msg": c["reason"]}
                for c in conflicts]

    CONCEDE_STEP = 0.75
    CONCEDE_FLOOR = 0.60

    def concede(self, req, verdict: str):
        """【协商闭环·发起方】差旅预算被财务守门人拒 → 降标准重报。

        只处理自己发起的 budget_request;其余回退默认(不让步)。
        每轮必须**真降**,且有下限 —— 否则会无限贴着守门人的红线来回试探。
        """
        if req.request_type != "budget_request":
            return super().concede(req, verdict)
        try:
            amount = float(req.payload.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None
        original = float(req.payload.get("_original_amount") or amount)
        new_amount = max(round(amount * self.CONCEDE_STEP), round(original * self.CONCEDE_FLOOR))
        if new_amount <= 0 or new_amount >= amount:
            return None
        p = dict(req.payload)
        p["amount"] = new_amount
        p["_original_amount"] = original
        p["_concession"] = f"降标准:{amount:.0f} → {new_amount:.0f}"
        return p

    def handle_cross_request(self, req) -> None:
        """日程占用申请 → 行程守门人检查冲突。"""
        if req.request_type != "schedule_request":
            return super().handle_cross_request(req)
        rt = getattr(self, "itin", None)
        if rt is None:
            req.status = "approved"; req.verdict = "行程内核未挂载,默认放行"; return
        new_time = req.payload.get("start_time")
        conflict = False
        if new_time:
            from datetime import datetime as _dt
            nt = _dt.fromisoformat(new_time)
            for wp in rt.waypoints:
                if wp.dt and abs((wp.dt - nt).total_seconds()) < 3600:
                    conflict = True
                    break
        if conflict:
            req.status = "needs_confirmation"
            req.verdict = "该时段附近已有行程,可能冲突,需确认"
        else:
            req.status = "approved"
            req.verdict = "时段空闲,日程可安排"

    def plan_itinerary(self, mode: str = "driving") -> dict:
        """调用行程内核做规划,关键洞察写入 ThoughtChain。"""
        rt = getattr(self, "itin", None)
        if rt is None:
            return {"error": "行程内核未挂载"}
        report = rt.plan(mode=mode)
        opt = report.get("optimized_route") or {}
        self._think(
            content=f"行程规划:{report['waypoint_count']} 个点,"
                    f"最优路线 {opt.get('total_km','?')} km / {opt.get('total_min','?')} 分钟,"
                    f"{len(report['conflicts'])} 个冲突,"
                    f"高德={'已接入' if report['amap_enabled'] else '估算模式'}",
            node_type="reflection", conf=0.9,
        )
        return report

    @staticmethod
    def _strip_knowledge_json(text: str) -> str:
        """从回复中剥离 {"new_knowledge": [...]} 结构化块,只保留自然语言。"""
        if not text or "new_knowledge" not in text:
            return text
        import re
        cleaned = re.sub(r'`{0,3}\s*\{\s*"new_knowledge".*?\}\s*\]\s*\}\s*`{0,3}',
                         '', text, flags=re.DOTALL)
        return cleaned.strip()

    def analyze_learning(self) -> dict:
        """调用学习内核做综合分析,关键洞察写入 ThoughtChain。"""
        rt = getattr(self, "learn_rt", None)
        if rt is None:
            return {"error": "学习内核未挂载"}
        report = rt.analyze()
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
        return """你是 Foxclaw 的办公学习 Agent，专注于办公、学习与日程安排。

你的核心职责：
1. 帮助用户学习新知识和技能
2. 管理知识储备，建立个人知识库
3. 处理文档、报告和写作任务
4. 跟踪学习进度和知识演化
5. 安排日程、会议与出行行程，检查时间冲突

Hermes 自学原则（像赫尔墨斯那样）：
- 不是学习更多技能，而是通过每次交互变得更"智能"
- 记住用户的学习风格和偏好
- 识别知识缺口，主动补充
- 建立知识间的连接，而不只是存储孤立事实
- 每次对话都让知识库更丰富、更有组织

日程/行程原则：
- 主动记住用户的时间偏好（如不喜欢早 8 点前的会议）
- 排期前先查已有行程，提前发现冲突
- 差旅预算议题与财务协商时，一轮一降、有下限，不硬闯

你的知识是活的，会生长的。"""

    _task_entities = lambda self, task: ["学习", "知识", "文档", "日程", "行程", "会议"]
    _recall_topk = 8

    def _recall_query(self, task: str) -> str:
        """学习 + 日程两类记忆都要召回(合并后单一 Agent 覆盖两个域)。"""
        return f"学习 知识 日程 行程 {task}"

    def _is_schedule_task(self, task: str) -> bool:
        return any(k in (task or "") for k in self.SCHEDULE_HINTS)

    async def _execute(self, task, context, session_id, memories):
        schedule = self._is_schedule_task(task)
        self._think(
            content=(f"{'日程/行程' if schedule else '知识'}任务：{task}，"
                     f"已有相关记忆：{len(memories)} 条"),
            node_type="reasoning",
            conf=0.92,
        )

        knowledge_context = ""
        if memories:
            knowledge_context = "相关知识储备：\n" + "\n".join([
                f"- {_fmt_mem(m, 80)}" for m in memories[:6]
            ])

        kernel_insight = ""
        learn_rt = getattr(self, "learn_rt", None)
        if learn_rt is not None and getattr(learn_rt, "items", None):
            try:
                report = learn_rt.analyze()
                due = report.get("due_today", 0)
                mastery = report.get("avg_mastery", 0)
                kernel_insight += (
                    f"\n\n【学习状态(基于真实知识库)】\n"
                    f"· 知识点总数: {report.get('total_items', 0)}\n"
                    f"· 平均掌握度: {mastery}\n"
                    f"· 今日待复习: {due} 个")
                self._think(
                    content=f"学习内核:{report.get('total_items',0)} 知识点,{due} 待复习",
                    node_type="reflection", conf=0.9,
                )
            except Exception:
                pass

        itin = getattr(self, "itin", None)
        if itin is not None and getattr(itin, "waypoints", None):
            try:
                report = itin.plan()
                opt = report.get("optimized_route") or {}
                conflicts = report.get("conflicts", [])
                parts = []
                if opt:
                    parts.append(f"· 最优路线: {opt.get('total_km','?')}km / "
                                 f"{opt.get('total_min','?')}分钟")
                if conflicts:
                    parts.append(f"· 检测到 {len(conflicts)} 个日程冲突")
                if parts:
                    kernel_insight += ("\n\n【行程分析结果(基于真实路点,请据此回答)】\n"
                                       + "\n".join(parts))
                    self._think(
                        content=f"行程内核:路线已优化,{len(conflicts)} 个冲突",
                        node_type="reflection", conf=0.9,
                    )
            except Exception:
                pass

        if schedule:
            prompt = f"""行程/日程任务：{task}

{knowledge_context}{kernel_insight}

请处理这个日程/行政任务，给出具体安排建议；有冲突就明说，别假装排开了。"""
        else:
            prompt = f"""学习/办公任务：{task}

{knowledge_context}{kernel_insight}

请：
1. 帮助完成这个学习/办公任务
2. 识别涉及的关键知识点
3. 指出哪些是新知识（需要记录）

回答后，以 JSON 格式附上需要记录的新知识：
{{"new_knowledge": [{{"title": "知识标题", "content": "知识内容", "tags": ["标签"]}}]}}"""

        response = await self._call_llm_web(task, [{"role": "user", "content": prompt}])

        new_knowledge_count = 0
        if not schedule:
            try:
                start = response.find('{"new_knowledge"')
                if start < 0:
                    start = response.find('{ "new_knowledge"')
                if start >= 0:
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
                pass

            response = self._strip_knowledge_json(response)

        self._think(
            content=(f"日程安排完成：{response[:120]}" if schedule
                     else f"学习完成，新增知识 {new_knowledge_count} 条"),
            node_type="reflection",
            conf=0.90,
        )
        if schedule:
            self._store_memory(
                content=f"日程任务：{task[:80]}\n安排：{response[:200]}",
                mem_type="execution",
                title=f"日程_{datetime.now().strftime('%Y%m%d')}",
                tags=["office", "schedule"],
            )
        return response, ({} if schedule else {"new_knowledge_stored": new_knowledge_count})
