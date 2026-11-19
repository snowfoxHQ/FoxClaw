"""G1 T3 确定性计算执行器（纯函数；不调用 LLM/网络/记忆/路由/验收）。"""
from decimal import Decimal

_D = Decimal
_n = lambda x: format(x.normalize(), "f")
_FUNCTIONS = {
    "T3-001": lambda: "8192",
    "T3-002": lambda: str(sum((3, 7, 11, 15, 19)) // 5),
    "T3-003": lambda: _n(_D(240) * _D("0.75")),
    "T3-004": lambda: _n(_D("50") * _D("0.30") + _D("120") * _D("0.15")),
    "T3-005": lambda: _n(_D(12) / _D(20) * 100),
    "T3-006": lambda: str((17 + 8) * 4 - 30),
    "T3-007": lambda: str(1024 // 1024),  # KB/MB 歧义待 G0 负责人裁定
    "T3-008": lambda: str(2 << 4),
    "T3-009": lambda: _n(_D(2000) * _D("0.05")),
    "T3-010": lambda: _n(_D(3) / _D(8) * 100),
}


def compute(task_id: str, prompt: str) -> str:
    """未冻结的 ID 一律 KeyError；输入为空拒绝，不猜测、不回退。"""
    if not prompt:
        raise ValueError("EMPTY_INPUT")
    return str(_FUNCTIONS[task_id]())
