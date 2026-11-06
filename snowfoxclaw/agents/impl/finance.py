"""FinanceAgent —— 财务（理财、收支、预算）。"""

from datetime import datetime

from ..base import CognitiveAgent
from ._helpers import _fmt_mem

class FinanceAgent(CognitiveAgent):
    AGENT_ID = "finance"
    AGENT_NAME = "财务 Agent"
    ALLOWED_TOOL_CATEGORIES = {"read", "write", "finance"}

    def attach_runtime(self, runtime):
        """挂载财务内核(FinanceRuntime):记忆+推理+Policy 守门人。"""
        self.fin = runtime

    # ── 编排协作(P3:财务领域态势 + 预算守门人,从编排层下沉至此)──────
    def situation(self) -> dict:
        rt = getattr(self, "fin", None)
        if rt is None:
            return {"domain": "finance", "status": {}, "alerts": []}
        try:
            over = [b for b in rt.reasoning.budget_status(rt.transactions, rt.budgets)
                    if b["is_over"]]
        except Exception:
            over = []
        status = {"transactions": len(rt.transactions),
                  "over_budget_categories": [b["category"] for b in over]}
        alerts = [{"domain": "finance", "severity": "warn",
                   "msg": f"{b['category']} 超支 {b['spent']:.0f}/{b['monthly_limit']:.0f}"}
                  for b in over]
        return {"domain": "finance", "status": status, "alerts": alerts}

    def handle_cross_request(self, req) -> None:
        """差旅/采购预算申请 → 财务守门人**认知化**裁决(v2 ③:观察→假设→证据→裁决,
        规则实现、裁决逐字不变、显式证据链)。"""
        if req.request_type != "budget_request":
            return super().handle_cross_request(req)
        rt = getattr(self, "fin", None)
        amount = req.payload.get("amount", 0)
        category = req.payload.get("category", "travel")
        if rt is None:
            req.status = "approved"; req.verdict = "财务内核未挂载,默认放行"; return
        from ...finance import Transaction
        from ...finance.reasoner import FinanceGatekeeperReasoner
        txn = Transaction(amount=-abs(amount), category=category,
                          entity=req.payload.get("entity", "差旅"),
                          account=req.payload.get("account", "default"))
        bal = rt.account_balances.get(txn.account)
        # Step3:模糊情形的 LLM 裁决(默认关;SNOWFOXCLAW_FINANCE_LLM_ARBITER=1 才启用)。
        # 安全不变量:LLM 只在规则已批准的模糊带被咨询、只能 approve→confirm、出错→confirm、永不 reject。
        import os as _os
        _llm_on = _os.environ.get("SNOWFOXCLAW_FINANCE_LLM_ARBITER", "") == "1"
        rv = FinanceGatekeeperReasoner(
            rt.policy, runtime=rt,
            llm=getattr(self, "router", None), llm_enabled=_llm_on).reason((txn, bal))
        req.status = {"approve": "approved", "confirm": "needs_confirmation",
                      "reject": "rejected"}[rv.decision]
        req.verdict = rv.rationale
        # 附证据链(可审计;新增字段,鸭子类型透传,不改既有行为)
        try:
            req.evidence = [e.to_dict() for e in rv.evidence]
            req.reason_source = rv.source
        except Exception:
            pass

    def analyze_finances(self, period: str = None, current_balance: float = None) -> dict:
        """调用财务内核做综合分析,并把关键洞察写入 ThoughtChain。"""
        fin = getattr(self, "fin", None)
        if fin is None:
            return {"error": "财务内核未挂载"}
        report = fin.analyze(period=period, current_balance=current_balance)
        # 把守门人裁决写进推理链(受 Harness 监督)
        verdicts = report.get("gatekeeper_verdicts", [])
        self._think(
            content=f"财务分析完成:{report['transaction_count']} 笔交易,"
                    f"{len(report.get('anomalies',[]))} 异常,{len(verdicts)} 条守门人裁决",
            node_type="reflection", conf=0.9,
        )
        return report

    @property
    def _role_prompt(self) -> str:
        return """你是 Snowfoxclaw 的财务 Agent，专注于个人财务管理。

你的核心职责：
1. 追踪收入和支出记录
2. 分析消费模式，给出理财建议
3. 制定储蓄和投资计划
4. 提供财务报告和趋势分析

工作原则：
- 所有财务建议都基于用户的历史记录和明确说明的情况
- 保守稳健，不推荐高风险操作
- 尊重用户的财务隐私
- 记住用户的财务目标和偏好，越用越了解

注意：你只能访问财务相关的工具和数据，不能操作删除功能。"""

    _task_entities = lambda self, task: ["财务", "收支", "储蓄"]
    _recall_query = lambda self, task: f"财务 {task}"

    async def _execute(self, task, context, session_id, memories):
            self._think(
                content=f"财务分析：{task}",
                node_type="reasoning",
                conf=0.93,
            )

            # 构建财务上下文
            finance_context = ""
            if memories:
                finance_context = "历史财务记录：\n" + "\n".join([
                    _fmt_mem(m, 100) for m in memories[:5]
                ])

            # 接入财务内核:若已挂载且有交易数据,做真实结构化分析,作为 LLM 的依据
            kernel_insight = ""
            fin = getattr(self, "fin", None)
            if fin is not None and getattr(fin, "transactions", None):
                try:
                    report = fin.analyze()
                    ec = report.get("evidence_chains", [])
                    anomalies = report.get("anomalies", [])
                    parts = []
                    for chain in ec:
                        parts.append(
                            f"· {chain['decision_type']}: 风险{chain['risk_score']}"
                            f"({chain['risk_level']}) — {chain.get('rationale','')};"
                            f"建议: {chain.get('recommended_action','')}")
                    if anomalies:
                        parts.append(f"· 检测到 {len(anomalies)} 笔异常支出")
                    if parts:
                        # 注:标签用产品级措辞(不带"内核"等内部实现词),避免 LLM 回复把内部
                        # 架构词汇透给终端用户(深层实现对用户保密;多 Agent 作卖点可讲)。
                        kernel_insight = ("\n\n【财务分析结果(基于真实交易,请据此回答)】\n"
                                          + "\n".join(parts))
                        # 把证据链摘要沉淀到 ThoughtChain(可审计)
                        self._think(
                            content=f"财务内核证据链:{len(ec)} 条决策,{len(anomalies)} 异常",
                            node_type="reflection", conf=0.9,
                        )
                except Exception:
                    pass   # 内核分析失败不阻断,降级为纯 LLM 回答

            response = await self._call_llm_web(task, [{
                "role": "user",
                "content": f"""财务任务：{task}

{finance_context}{kernel_insight}

请提供专业的财务分析和建议。如果需要记录具体数字，请以结构化格式返回。"""
            }])

            self._think(
                content=f"财务分析完成：{response[:150]}",
                node_type="decision",
                conf=0.90,
            )

            # 记忆财务决策
            self._store_memory(
                content=f"财务分析：{task[:80]}\n结论：{response[:200]}",
                mem_type="decision",
                title=f"财务_{datetime.now().strftime('%Y%m%d')}",
                tags=["finance", "analysis"],
            )
            return response, {}

