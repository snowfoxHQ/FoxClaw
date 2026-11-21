"""个人数据出网判定 —— 唯一真源。

凡是"要不要把这个问题发给外部"的判断都走这里:技能路由的外部技能闸
(pipeline.cognitive_pipeline)、以及历史上联网搜索的触发判定。

教训:这条保护原先只写在联网触发里,技能派发默认开启后,
「查一下最新的本月支出」照样把私人财务问题发去外部 —— 同一个 bug
从没设防的旁路回来了。保护必须放在共同咽喉,不能各写一份。
"""
from __future__ import annotations

PERSONAL_MARKERS = [
    "支出", "收入", "预算", "交易", "账单", "花了", "消费", "记账", "结余", "余额",
    "行程", "日程", "出行", "复习", "知识点", "学习计划", "记忆",
]


def is_personal_query(query: str) -> bool:
    """问的是不是**用户自己的数据**(账本/行程/学习/记忆)。"""
    if not query:
        return False
    return any(m in query.lower() for m in PERSONAL_MARKERS)
