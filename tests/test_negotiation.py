"""智能体层深化·跨域协商闭环:被守门人拒 → 发起方降标准重报 → 再裁决。

原缺口:cross_request 是**一次性**的(路由→拿裁决→return)。「下周去上海出差」→ 行程排程
→ 财务守门人预算不足 → rejected → **到此为止**,用户只拿到一句"被拒绝"。

安全不变量(比功能更重要):
  · 裁决权仍在目标 Agent 的守门人,协商**不得成为绕过手段**
  · 协商失败**维持 rejected**,绝不降级成 needs_confirmation(否则等于开了个点穿守门人的口子)
  · 有界 + 必须真让步(否则无限贴红线试探)
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_negotiation():
    os.environ["HKC_EMBEDDING"] = "stub"
    os.environ["FOXCLAW_SHADOW_ROUNDS"] = "6"

    from foxclaw.orchestration import CrossAgentRequest, Orchestrator

    print("=" * 60); print("跨域协商闭环验证"); print("=" * 60)


    class _Gate:
        """假目标 Agent:预算 <= limit 才批(模拟财务守门人)。"""
        def __init__(self, limit): self.limit = limit; self.seen = []
        def handle_cross_request(self, req):
            amt = float(req.payload.get("amount", 0))
            self.seen.append(amt)
            if amt <= self.limit:
                req.status = "approved"; req.verdict = f"预算 {amt:.0f} 在额度内"
            else:
                req.status = "rejected"; req.verdict = f"预算不足:申请 {amt:.0f} > 可用 {self.limit:.0f}"


    class _Requester:
        """假发起方:每轮降 25%,夹到原始的 floor 倍;到底则认输(与 AdminAgent.concede 同款)。"""
        def __init__(self, floor=0.6): self.floor = floor; self.calls = 0
        def concede(self, req, verdict):
            self.calls += 1
            amt = float(req.payload.get("amount", 0))
            orig = float(req.payload.get("_original_amount") or amt)
            new = max(round(amt * 0.75), round(orig * self.floor))
            if new <= 0 or new >= amt:
                return None
            p = dict(req.payload); p["amount"] = new; p["_original_amount"] = orig
            return p


    class _Caps:
        """假能力注册中枢:Orchestrator 经它惰性解析 agents(P1-3 打断对象环)。"""
        def __init__(self, agents): self._a = agents
        def get(self, name): return self._a if name == "agents" else None


    def _orch(agents):
        return Orchestrator(_Caps(agents))              # 只测协议,不拉起整个 OS


    def _req(amount=8000):
        return CrossAgentRequest(from_agent="admin", to_agent="finance",
                                 request_type="budget_request", payload={"amount": amount})


    # [1] 核心场景:被拒 → 让步 → 通过(原先到 rejected 就结束了)
    gate = _Gate(limit=5000); r = _orch({"finance": gate, "admin": _Requester()})
    req = r.negotiate(_req(8000))
    assert req.status == "approved", (req.status, req.verdict)
    assert req.payload["amount"] <= 5000, req.payload
    assert len(req.negotiation) >= 2, "应发生过至少一轮让步"
    assert gate.seen[0] == 8000 and gate.seen[-1] <= 5000, gate.seen
    print(f"[1] 被拒→让步→通过({' → '.join(str(int(a)) for a in gate.seen)})OK")

    # [2] 让步记录可追溯(每轮裁决 + 让到多少)
    n = req.negotiation
    assert n[0]["status"] == "rejected" and "conceded_to" in n[0], n[0]
    assert n[-1]["status"] == "approved", n[-1]
    print(f"[2] 协商日志可追溯({len(n)} 轮,含每轮裁决与让步)OK")

    # [3] 安全:让无可让 → **维持 rejected**,绝不降级成 needs_confirmation
    gate2 = _Gate(limit=100); r2 = _orch({"finance": gate2, "admin": _Requester()})
    req2 = r2.negotiate(_req(8000))
    assert req2.status == "rejected", f"协商失败必须维持 rejected,不得降级: {req2.status}"
    print("[3] 让无可让 → 维持 rejected(不给点穿守门人的口子)OK")

    # [4] 有界:轮数不超过 max_rounds(防死循环)
    gate3 = _Gate(limit=1); r3 = _orch({"finance": gate3, "admin": _Requester(floor=0.0)})
    req3 = r3.negotiate(_req(8000), max_rounds=3)
    assert len(gate3.seen) <= 3, f"轮数应有界: {gate3.seen}"
    assert req3.status == "rejected"
    print(f"[4] 有界协商(裁决 {len(gate3.seen)} 次 ≤ max_rounds=3)OK")

    # [5] 必须真让步:concede 返回原样 payload → 立即停(否则无限重复同一请求)
    class _NoOp:
        def concede(self, req, verdict): return dict(req.payload)     # 假让步
    gate4 = _Gate(limit=1); r4 = _orch({"finance": gate4, "admin": _NoOp()})
    r4.negotiate(_req(8000), max_rounds=5)
    assert len(gate4.seen) == 1, f"无实质让步应只裁决一次: {gate4.seen}"
    print("[5] 假让步(payload 无变化)→ 立即停,不重复轰炸守门人 OK")

    # [6] 不让步的 Agent(默认 concede→None)→ 与协商前逐字等价:只跑一轮
    class _Silent:
        def concede(self, req, verdict): return None
    gate5 = _Gate(limit=1); r5 = _orch({"finance": gate5, "admin": _Silent()})
    req5 = r5.negotiate(_req(8000))
    assert len(gate5.seen) == 1 and req5.status == "rejected"
    print("[6] 未实现让步的 Agent → 只跑一轮(零行为变更)OK")

    # [7] 让步逻辑抛异常 → 停 + 维持原裁决(不因发起方 bug 放行)
    class _Boom:
        def concede(self, req, verdict): raise RuntimeError("concede 崩了")
    gate6 = _Gate(limit=1); r6 = _orch({"finance": gate6, "admin": _Boom()})
    req6 = r6.negotiate(_req(8000))
    assert req6.status == "rejected", "发起方 concede 崩溃不得影响裁决"
    assert any("concede_error" in x for x in req6.negotiation)
    print("[7] 让步逻辑崩溃 → 维持 rejected + 记录错误 OK")

    # ── 真 OfficeAgent 的让步策略(行政并入办公,领域逻辑仍归领域 Agent)──
    from foxclaw.agents.agents import OfficeAgent
    adm = OfficeAgent.__new__(OfficeAgent)

    # [8] OfficeAgent(原 AdminAgent):逐轮降档 → 夹到下限(600)→ 到底认输。全程单调下降、必然终止
    p = adm.concede(_req(1000), "预算不足")
    assert p and p["amount"] == 750 and "降标准" in p["_concession"], p
    seq, cur = [1000], _req(1000).payload
    while True:                                    # 走完整条让步链,证明单调且会终止
        nxt = adm.concede(_req(0).__class__(from_agent="admin", to_agent="finance",
                                            request_type="budget_request", payload=dict(cur)), "拒")
        if nxt is None:
            break
        assert nxt["amount"] < cur["amount"], "让步必须单调下降"
        cur = nxt; seq.append(cur["amount"])
        assert len(seq) < 20, "让步链必须终止"
    assert seq[-1] == 600, f"应走到下限 600(原始 1000 的 60%),实际 {seq}"
    print(f"[8] OfficeAgent 让步链 {seq} → 到下限认输(单调下降·必然终止)OK")

    # [9] OfficeAgent:非预算请求不插手(回退默认不让步)
    other = CrossAgentRequest(from_agent="admin", to_agent="office",
                              request_type="knowledge_request", payload={"q": "x"})
    assert adm.concede(other, "拒") is None
    print("[9] 非 budget_request → 不插手(回退默认)OK")

    print("\n跨域协商闭环验证全部通过")

if __name__ == "__main__":
    test_negotiation()
