"""多步 dispatch 结果判定谓词（P0-1 反思重规划的前提条件）。

**为什么放在 core**：`pipeline` 的 replan stage 需要判断「上一步是否失败」，但
`pipeline` 不能反向 import `agents` —— 那会形成 `agents ↔ pipeline` 包级环
（`pipeline/supervisor_stages.py` 曾用「函数内局部 import」回避，注释里也写明了）。
谓词下沉到这里后，两侧都只依赖 `core`，依赖方向单一：

    agents ──▶ core ◀── pipeline

纯函数、无副作用、不 import 任何上层模块。
"""


def dispatch_failed(dr) -> bool:
    """判断多步 dispatch 结果是否含失败/未完成步骤（触发反思重规划的前提）。"""
    if not isinstance(dr, dict):
        return False
    result = dr.get("result") or {}
    if not isinstance(result, dict):
        return False
    if result.get("dispatched_to") not in ("multi", "parallel"):
        return False
    if result.get("blocked"):
        return True
    steps = result.get("parallel_steps") or result.get("multi_steps") or []
    return any((s.get("failed") or s.get("blocked"))
               for s in steps if isinstance(s, dict))
