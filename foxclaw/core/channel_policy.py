"""三通道选择机制 — Runtime 侧确定性决策(基准 v1.0 §L2)。

三条硬规则(冻结基准,不得偏离):
  1. **声明只是计划,不是执行许可**:模型/计划声明首选通道后,由这里的确定性
     规则做能力裁决;授权维度仍由 L3(PolicyGate)独立裁决,**授权结果绝不作为
     本模块的输入**——"程序化接口没授权"不是降级理由,换通道同样要过 L3。
  2. **降级必须有探测证据**:UIA 树里没有可用元素,是能力探测结果,不是权限拒绝;
     没跑过探测就跳到 Visual,一律拒绝(截图是最有损、最贵的通道)。
  3. **通道切换不产生授权豁免**:本模块只决定"用哪条通道",每次操作的准入
     仍由 desktop_act/check_action 各自完成,与改前完全一致。

探测证据形态(存在 TaskContext.channel_probes):
  {"channel": "semantic", "ok": True/False, "detail": str, "ts": float}
  · ok=True  → 探测执行且通过
  · ok=False → 探测**执行过**且失败(这是降级到 Visual 的唯一合法证据)
  · 缺失     → 未探测(resolve 会要求先探测,visual_gate 会拒绝 Visual)
"""
from __future__ import annotations

PROGRAMMATIC = "programmatic"
SEMANTIC = "semantic"
VISUAL = "visual"
CHANNELS = (PROGRAMMATIC, SEMANTIC, VISUAL)

PROBE_LIMIT = 20  # 探测证据有界,长任务不当内存放大器


def channel_of_action(action: str) -> str:
    """桌面动作 → 操作通道(通道门控只关心 UI 交互;未知动作返回空串=不参与门控)。

    · ui:*            → Semantic(UIA 元素树,语义操作)
    · mouse/keyboard/window:* → Visual(像素坐标/键鼠)
    · file/shell/clipboard:* → Programmatic(程序状态)
    """
    cat = str(action or "").split(":", 1)[0]
    if cat == "ui":
        return SEMANTIC
    if cat in ("mouse", "keyboard", "window"):
        return VISUAL
    if cat in ("file", "shell", "clipboard"):
        return PROGRAMMATIC
    return ""


def _pref_ok(probes, channel) -> bool:
    return any(p.get("channel") == channel and p.get("ok") is True for p in probes)


def _pref_failed(probes, channel) -> bool:
    return any(p.get("channel") == channel and p.get("ok") is False for p in probes)


def resolve(preferred: str, fallback: str, probes: list) -> dict:
    """确定性通道决策(Runtime 决策点,能力维度)。

    返回 {"channel": str|None, "degraded": bool, "reason": str, "evidence": [...]}:
    · channel=None 且 reason 以 "probe_required:" 开头 → 先执行该通道探测再决策;
    · channel=None 且 degraded=False → 无可用通道(blocked),交回上层/用户;
    · degraded=True → 发生降级,evidence 里带触发降级的探测失败记录。
    Visual 无需探测(Windows 上截图通道恒可用);其余通道必须有探测证据。
    """
    probes = probes or []
    pref = preferred if preferred in CHANNELS else PROGRAMMATIC  # 非法声明按优先序兜底
    fb = fallback if fallback in CHANNELS else ""
    if fb == pref:
        fb = ""
    ev = [p for p in probes if p.get("channel") in (pref, fb)]

    if pref == VISUAL:  # 截图通道恒可用,声明即用
        return {"channel": VISUAL, "degraded": False, "reason": "声明首选 Visual", "evidence": ev}
    if _pref_ok(probes, pref):
        return {"channel": pref, "degraded": False, "reason": f"{pref} 探测通过", "evidence": ev}
    if not _pref_failed(probes, pref):
        return {"channel": None, "degraded": False, "evidence": ev,
                "reason": f"probe_required:{pref}(先执行首选通道能力探测,再决策)"}
    # 首选探测已执行且失败 → 检查降级条件
    if fb == VISUAL:
        return {"channel": VISUAL, "degraded": True, "evidence": ev,
                "reason": f"{pref} 探测失败(有证据),按声明降级 Visual"}
    if fb and _pref_ok(probes, fb):
        return {"channel": fb, "degraded": True, "evidence": ev,
                "reason": f"{pref} 探测失败,{fb} 探测通过,降级 {fb}"}
    if fb:
        return {"channel": None, "degraded": False, "evidence": ev,
                "reason": f"probe_required:{fb}({pref} 已失败,降级前先探测 {fb})"}
    return {"channel": None, "degraded": False, "evidence": ev,
            "reason": f"{pref} 探测失败且未声明降级通道,无可用通道"}


def visual_gate(plan, probes, action: str) -> str:
    """执行前确定性校验:声明了非 Visual 首选时,Visual 动作必须有降级证据。

    返回空串=放行;返回非空=拒绝原因(确定性规则,不是授权裁决,不影响 L3)。
    · 无声明 → 不干预(声明制渐进引入,行为与改前一致);
    · 只门控 Visual 通道动作(mouse/keyboard/window:*);ui:*/file:* 不在此列;
    · 放行条件:声明首选即 Visual,或存在「首选通道探测已执行且失败」的证据。
    """
    if not isinstance(plan, dict) or not plan.get("preferred"):
        return ""
    if channel_of_action(action) != VISUAL:
        return ""
    pref = plan.get("preferred")
    if pref == VISUAL:
        return ""
    if _pref_failed(probes or [], pref):
        return ""
    return (f"通道决策:子目标声明首选通道 {pref},降级到 Visual 需先执行 {pref} 通道"
            f"能力探测(如 ui:locate/ui:tree)且失败;当前无探测证据,不得凭猜测直接 Visual")


def record_probe(tc, channel: str, ok: bool, detail: str = "") -> None:
    """把一次能力探测结果落进 TaskContext(fail-safe,授权结果绝不进这里)。"""
    if tc is None or channel not in CHANNELS:
        return
    try:
        probes = list(tc.to_dict().get("channel_probes") or [])
        if len(probes) >= PROBE_LIMIT:
            return
        tc.add_probe(channel, bool(ok), str(detail or "")[:200])
    except Exception:
        pass
