"""Finish Gate — 任务终局的确定性核验(基准 v1.0 §L4)。

三个架构决定(冻结基准,不得偏离):
  · **不引入第二个模型**:这里没有任何 LLM 调用,只做确定性检查;
  · **验证器不消费模型的自我报告**:检查对象是执行期间落账的产物引用(artifacts,
    由 _settle 在核验通过的 file 写/删步骤自动落账,不是模型声明的);
  · **三态诚实**:PASS=确定性检查全过;FAIL=任一确定性检查失败(任务不得进入
    成功终态);UNKNOWN=没有可确定性检查的对象(诚实上报,不许装作 PASS)。

能力边界(如实,不许夸大):本 Gate 只验证**结构性正确**(产物文件存在/非空/
已删除),不验证语义正确——文件存在不代表计算结果对。语义验收(goal 约束条目)
在 TaskContext 中保持 unknown,交由用户判断,绝不假装 pass。

v1 范围:产物级结构检查。验收条件(criteria)→ 确定性检查器的映射在 G3 只覆盖
file 类 artifacts;后续扩展检查器种类时在这里注册,不另建验证引擎。
"""
from __future__ import annotations

import os
import time

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

MAX_FAILURES = 20


def evaluate(tc) -> dict:
    """对 TaskContext 做终局确定性评估,返回结构化报告(fail-safe:不抛异常)。

    报告结构:
      {"status": PASS|FAIL|UNKNOWN, "checked": n,
       "failures": [{"check", "ref", "reason"}], "at": ts}
    """
    out = {"status": UNKNOWN, "checked": 0, "failures": [], "at": time.time()}
    try:
        artifacts = (tc.to_dict().get("artifacts") or []) if tc is not None else []
    except Exception:
        return out
    checked = 0
    failures: list = []
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("kind") or "")
        ref = str(a.get("ref") or "")
        if not ref:
            continue
        if kind == "file":
            checked += 1
            why = _file_present(ref)
            if why:
                failures.append({"check": "file_artifact", "ref": ref, "reason": why})
        elif kind == "file_deleted":
            checked += 1
            try:
                if os.path.exists(ref):
                    failures.append({"check": "file_deleted", "ref": ref,
                                     "reason": f"声明已删除的文件仍然存在:{ref}"})
            except OSError:
                failures.append({"check": "file_deleted", "ref": ref,
                                 "reason": f"无法核验删除状态:{ref}"})
        if len(failures) >= MAX_FAILURES:
            break
    out["checked"] = checked
    out["failures"] = failures[:MAX_FAILURES]
    # FAIL 优先如实上报;全过且有检查对象才算 PASS;没有可查对象就是 UNKNOWN
    out["status"] = FAIL if failures else (PASS if checked else UNKNOWN)
    return out


def _file_present(ref: str) -> str:
    """产物文件的确定性检查:存在且非空。返回失败原因,空串=通过。"""
    try:
        if not os.path.exists(ref):
            return f"产物文件不存在:{ref}"
        if os.path.isfile(ref) and os.path.getsize(ref) == 0:
            return f"产物文件为空:{ref}"
    except OSError as e:
        return f"无法核验产物文件:{ref}({type(e).__name__})"
    return ""


def reject_message(report: dict) -> str:
    """FAIL 时的结构化失败原因,交回主 Agent/用户可读的一行摘要。"""
    fails = report.get("failures") or []
    detail = ";".join(str(f.get("reason") or "") for f in fails[:5])
    return f"[完成核验] 结构检查未通过({detail}),任务不标记为完成。"
