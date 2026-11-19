"""G1 确定性执行器：冻结白名单 10 项精确结果、未知 ID/空输入拒绝。"""
import pytest

from foxclaw.g1.python_numeric import compute

EXPECTED = {
    "T3-001": "8192", "T3-002": "11", "T3-003": "180", "T3-004": "33",
    "T3-005": "60", "T3-006": "70", "T3-007": "1", "T3-008": "32",
    "T3-009": "100", "T3-010": "37.5",
}


@pytest.mark.parametrize("task_id,expected", list(EXPECTED.items()))
def test_exact_results(task_id, expected):
    out = compute(task_id, "原始任务输入")
    assert out == expected and isinstance(out, str)


def test_unknown_and_empty_rejected():
    with pytest.raises(KeyError):
        compute("T9-999", "任意")
    with pytest.raises(ValueError):
        compute("T3-001", "")
