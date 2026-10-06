"""AdminAgent —— 行政（日程、行程、会议）。"""

from datetime import datetime

from ..base import CognitiveAgent
from ._helpers import _fmt_mem

class AdminAgent(CognitiveAgent):
    AGENT_ID = "admin"
    AGENT_NAME = "行政 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "calendar"}

    def attach_runtime(self, runtime):
        """挂载行程内核(ItineraryRuntime):路线优化 + 可达性 + 守门人。"""
        self.itin = runtime

    # ── 编排协作(P3:行程领域态势 + 日程守门人,从编排层下沉至此)──────
    def situation(self) -> dict:
        rt = getattr(self, "itin", None)
        if rt is None:
            return {"domain": "itinerary", "status": {}, "alerts": []}
        try:
            conflicts = rt.reasoning.detect_conflicts(rt.waypoints)
        except Exception:
            conflicts = []
        status = {"waypoints": len(rt.waypoints), "conflicts": len(conflicts)}
        alerts = [{"domain": "itinerary", "severity": "critical", "msg": c["reason"]}
                  for c in conflicts]
        return {"domain": "itinerary", "status": status, "alerts": alerts}

    # 差旅预算让步:每轮降一档;低于原始的 60% 就不再让(那已不是同一趟出差了,
    # 硬压下去只会规划出一个没法执行的行程 —— 宁可认输转由人决定)。
    CONCEDE_STEP = 0.75          # 每轮降到上一轮的 75%(改高铁/换酒店/减一天的量化近似)
    CONCEDE_FLOOR = 0.60         # 相对**原始**申请额的下限

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
        # 降一档,但**夹到下限**而不是跌破就放弃 —— 否则步长过冲时会在下限之上提前认输
        # (8000→6000→(4500 破线)放弃),白白浪费 4800 这个本来能过的方案。
        new_amount = max(round(amount * self.CONCEDE_STEP), round(original * self.CONCEDE_FLOOR))
        if new_amount <= 0 or new_amount >= amount:
            return None                      # 已到下限,再降就破线 → 认输(维持 rejected,不硬闯)
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

    @property
    def _role_prompt(self) -> str:
        return """你是 Hestia OS 的行政 Agent，专注于日程和行程管理。

你的核心职责：
1. 管理日历事件和会议安排
2. 规划出行行程和路线
3. 维护待办事项和提醒
4. 处理行政文档和表格

工作原则：
- 主动记住用户的时间偏好（如不喜欢早 8 点前的会议）
- 提前检测日程冲突
- 整合用户的习惯模式给出智能建议
- 越用越了解用户的生活节奏"""

    _task_entities = lambda self, task: ["日历", "行程", "会议"]
    _recall_query = lambda self, task: f"行程 日程 {task}"

    async def _execute(self, task, context, session_id, memories):
            self._think(
                content=f"行政处理：{task}",
                node_type="reasoning",
                conf=0.91,
            )

            schedule_context = ""
            if memories:
                schedule_context = "历史日程记录：\n" + "\n".join([
                    _fmt_mem(m, 100) for m in memories[:5]
                ])

            # 接入行程内核:若已挂载且有路点,做真实规划(路线/冲突)作为 LLM 依据
            kernel_insight = ""
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
                        kernel_insight = ("\n\n【行程分析结果(基于真实路点,请据此回答)】\n"
                                          + "\n".join(parts))
                        self._think(
                            content=f"行程内核:路线已优化,{len(conflicts)} 个冲突",
                            node_type="reflection", conf=0.9,
                        )
                except Exception:
                    pass   # 内核失败不阻断,降级纯 LLM

            response = await self._call_llm_web(task, [{
                "role": "user",
                "content": f"""行程任务：{task}

{schedule_context}{kernel_insight}

请处理这个行政/日程任务，给出具体安排建议。"""
            }])

            self._think(
                content=f"行程安排：{response[:150]}",
                node_type="decision",
                conf=0.88,
            )

            self._store_memory(
                content=f"行程任务：{task[:80]}\n安排：{response[:200]}",
                mem_type="execution",
                title=f"日程_{datetime.now().strftime('%Y%m%d')}",
                tags=["admin", "schedule"],
            )
            return response, {}

