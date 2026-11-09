"""自然语言记账解析 —— 把「今天买了一件衣服花了 105 块」变成一笔结构化交易。

财务应用的第一线输入几乎全是这种大白话:没有 CSV、没有表单,用户只是随口报备一句。
这里用**确定性规则**(正则 + 中文数字 + 关键词表)解析,不让 LLM 猜金额 —— 记错一笔钱
比慢 100 毫秒严重得多,而且金额出错会顺着预算/风控/预测一路错下去。

两个刻意的顺序:

1. **先剥时间,再找金额**。否则「上个月 5 号买衣服 100」里的 5 会被当成金额。
2. **先认带货币单位的数字,再退回动词旁的裸数字**。否则「买了 2 件衣服 105」会记成 2 块。

时态:
- 今天/昨天/前天/N 号/上个月 … → 已发生(settled)
- 明天/后天/大后天/下个月 … → 计划支出(planned)。用户说「明天交房租 3000」是希望
  被记住,不是希望被忽略,但它绝不能算进「本月已花」。

币种:「105 块 / 105 元 / 105 人民币 / 105 RMB」→ CNY,「105 美元 / 105 美金 / $105」
→ USD,另有港币/欧元/日元/英镑等。识别出的原币金额与币种都保留,折算成本位币的
汇率由 `fx.py` 提供(静态参考价,可改),不联网也不假装实时。
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

# ── 币种 ──────────────────────────────────────────────────
# 别名按长度倒序匹配:"块钱" 必须先于 "块","人民币" 先于 "元",否则会切错。
CURRENCY_ALIASES = {
    "人民币": "CNY", "块钱": "CNY", "块": "CNY", "元": "CNY", "圆": "CNY",
    "软妹币": "CNY", "rmb": "CNY", "cny": "CNY",
    "美元": "USD", "美金": "USD", "美刀": "USD", "刀": "USD", "usd": "USD",
    "港元": "HKD", "港币": "HKD", "hkd": "HKD",
    "欧元": "EUR", "eur": "EUR",
    "日元": "JPY", "日币": "JPY", "jpy": "JPY",
    "英镑": "GBP", "gbp": "GBP",
    "韩元": "KRW", "krw": "KRW",
    "新币": "SGD", "新加坡元": "SGD", "sgd": "SGD",
    "澳元": "AUD", "aud": "AUD",
    "加元": "CAD", "cad": "CAD",
}
_SYMBOLS = {"¥": "CNY", "￥": "CNY", "$": "USD", "€": "EUR", "£": "GBP", "₩": "KRW"}
_UNIT_ALT = "|".join(re.escape(u) for u in
                     sorted(CURRENCY_ALIASES, key=len, reverse=True))

# 倍数后缀:1.5k / 2w / 3万 —— 不是币种,只是量级
_SCALE = {"k": 1000, "千": 1000, "w": 10000, "万": 10000}

# ── 中文数字 ──────────────────────────────────────────────
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
_CN_NUM = "[零〇一二两三四五六七八九十百千万亿]{1,12}"

# ── 量词:数字 + 量词是"几件/2 袋",不是金额,必须先剔除 ─────────
# 注意「块」不在此表 —— 它是货币单位(105 块),不是量词。
# 「人」「位」故意不收:会吃掉「105 人民币」的「人」。
_QUANTIFIERS = ("件|个|只|双|条|包|袋|瓶|杯|斤|公斤|kg|克|张|台|部|本|支|盒|箱|"
                "次|份|碗|串|根|桶|套|顿|晚|小时|分钟|公里|千米|米|升|码|岁|ml")
_QUANT_PAT = re.compile(rf"(?:\d[\d,.]*|{_CN_NUM})\s*(?:{_QUANTIFIERS})", re.I)

# ── 花费/收入动词 ────────────────────────────────────────
_EXPENSE_VERBS = ("花了", "花费", "花", "付了", "付款", "支付", "买了", "买", "购",
                  "消费", "充值", "充了", "交", "缴", "扣", "订", "租", "打车", "吃")
_INCOME_VERBS = ("收入", "工资", "发了", "发工资", "收到", "收款", "进账", "退款",
                 "退回", "报销到账", "报销了", "赚", "奖金", "红包", "分红", "利息", "中奖")

_VERB_PAT = re.compile("|".join(sorted(_EXPENSE_VERBS, key=len, reverse=True)))
_INCOME_PAT = re.compile("|".join(sorted(_INCOME_VERBS, key=len, reverse=True)))

# ── 分类关键词(顺序即优先级:订阅要先于娱乐,否则「腾讯视频会员」会归到娱乐)──
CATEGORY_LABELS = {
    "food": "餐饮食品", "clothing": "服饰", "transport": "交通出行",
    "housing": "居住", "subscription": "订阅服务", "entertainment": "娱乐",
    "medical": "医疗健康", "education": "教育", "digital": "数码家电",
    "pet": "宠物", "travel": "旅行", "income": "收入", "misc": "其他",
}
_CATEGORY_RULES = [
    ("subscription", ["会员", "订阅", "vip", "netflix", "网飞", "腾讯视频",
                      "爱奇艺", "bilibili", "b站大会员", "spotify", "icloud", "wps会员"]),
    ("food", ["大米", "米面", "面粉", "菜", "肉", "排骨", "水果", "奶茶", "咖啡",
              "饭", "早餐", "午餐", "晚餐", "夜宵", "外卖", "零食", "超市", "牛奶",
              "鸡蛋", "面包", "饮料", "火锅", "烧烤", "酒", "水饺", "面条", "油盐",
              # 常见餐饮商户:用户报备时往往只说店名
              "星巴克", "瑞幸", "麦当劳", "肯德基", "海底捞", "喜茶", "蜜雪", "食堂"]),
    ("clothing", ["衣服", "裤子", "鞋", "外套", "裙子", "衬衫", "t恤", "帽子",
                  "包", "袜子", "内衣", "羽绒服", "西装", "围巾", "手套", "牛仔裤"]),
    ("transport", ["洗车", "加油", "油费", "打车", "滴滴", "出租车", "公交", "地铁",
                   "高铁", "火车", "机票", "停车", "过路费", "保养", "修车", "充电桩"]),
    ("housing", ["房租", "水电", "电费", "水费", "物业", "燃气", "煤气", "暖气", "宽带"]),
    ("medical", ["药", "医院", "挂号", "体检", "牙", "看病", "门诊", "住院"]),
    ("education", ["书", "课程", "学费", "培训", "网课", "考试", "文具", "教材", "报班"]),
    ("digital", ["手机", "电脑", "耳机", "充电器", "数据线", "硬盘", "显示器", "键盘", "鼠标"]),
    ("pet", ["猫粮", "狗粮", "宠物", "猫砂", "疫苗", "猫", "狗"]),
    ("travel", ["旅游", "景区", "门票", "酒店", "民宿", "度假", "签证"]),
    ("entertainment", ["电影", "游戏", "ktv", "演出", "演唱会", "剧本杀", "健身", "按摩"]),
]

# ── 时间表达 ─────────────────────────────────────────────
_DAY_WORDS = [  # 长的先匹配,避免「大后天」被「后天」吃掉
    ("大后天", 3), ("大前天", -3), ("后天", 2), ("前天", -2),
    ("昨天", -1), ("昨日", -1), ("昨晚", -1), ("昨儿", -1),
    ("今天", 0), ("今日", 0), ("今儿", 0), ("刚刚", 0), ("刚才", 0),
    ("明天", 1), ("明日", 1), ("明儿", 1),
]
_MONTH_WORDS = [  # (词, 月偏移)
    ("上上个月", -2), ("上上月", -2), ("上个月", -1), ("上一个月", -1),
    ("上月", -1), ("下下个月", 2), ("下下月", 2), ("下个月", 1),
    ("下一个月", 1), ("下月", 1), ("这个月", 0), ("本月", 0), ("这月", 0),
]
_MONTH_ALT = "|".join(re.escape(w) for w, _ in
                      sorted(_MONTH_WORDS, key=lambda x: -len(x[0])))
_WEEK_WORDS = [("上周", -7), ("上星期", -7), ("上个星期", -7),
               ("这周", 0), ("本周", 0), ("这星期", 0),
               ("下周", 7), ("下星期", 7), ("下个星期", 7)]


# ══════════════════════════════════════════════════════════
# 解析结果
# ══════════════════════════════════════════════════════════
@dataclass
class ParsedExpense:
    """一句话里解析出的一笔钱(还没落账)。"""
    text: str                     # 原始片段
    direction: str = "expense"    # expense / income
    amount: float = 0.0           # 原币金额(正数)
    currency: str = "CNY"
    when: datetime = field(default_factory=datetime.now)
    category: str = "misc"
    description: str = ""         # 买了什么
    entity: str = ""              # 商户/物品(账本里按它归类)
    account: str = "default"
    confidence: float = 0.5
    warnings: list = field(default_factory=list)
    planned: bool = False         # 未来时间 → 计划支出,不进"已发生"汇总

    @property
    def is_planned(self) -> bool:
        return self.planned

    def to_dict(self) -> dict:
        return {
            "text": self.text, "direction": self.direction,
            "amount": round(self.amount, 2), "currency": self.currency,
            "when": self.when.isoformat(timespec="seconds"),
            "is_planned": self.is_planned, "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "description": self.description, "entity": self.entity,
            "account": self.account, "confidence": round(self.confidence, 2),
            "warnings": self.warnings,
        }


# ══════════════════════════════════════════════════════════
# 中文数字
# ══════════════════════════════════════════════════════════
def cn_to_number(s: str) -> Optional[float]:
    """「一百零五」→105、「两千三」→2300、「十万」→100000。解析不了返回 None。

    口语省略规则必须处理,否则「两千三」会被读成 2003:
    末尾的裸数字紧跟在单位后 = 下一级单位(千→百、百→十),而「零」之后的裸数字 = 个位。
    """
    if not s:
        return None
    if s.isdigit():
        return float(s)
    total, section, num, last_unit, zero = 0, 0, 0, 0, False
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            if num == 0:
                zero = True
            continue
        unit = _CN_UNITS.get(ch)
        if unit is None:
            return None
        if unit >= 10000:
            section = (section + num) * unit
            total += section
            section, num, last_unit = 0, 0, unit
        else:
            if num == 0:
                num = 1
            section += num * unit
            num, last_unit = 0, unit
    if num:
        if zero:                              # 一百零五
            section += num
        elif last_unit >= 10:                 # 两千三 → 2300
            section += num * max(1, last_unit // 10)
        else:
            section += num
    return float(total + section)


# ══════════════════════════════════════════════════════════
# 时间
# ══════════════════════════════════════════════════════════
def _shift_month(now: datetime, months: int, day: Optional[int] = None) -> datetime:
    """按月份偏移,并把日 clamp 到目标月的合法范围(1 月 31 日 −1 月 → 2 月 28/29)。"""
    y, m = now.year, now.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    d = day if day is not None else now.day
    while d > 28:
        try:
            return datetime(y, m, d, now.hour, now.minute)
        except ValueError:
            d -= 1
    return datetime(y, m, d, now.hour, now.minute)


def _month_days(y: int, m: int) -> int:
    nxt = datetime(y + (m == 12), (m % 12) + 1, 1)
    return (nxt - timedelta(days=1)).day


def _parse_when(text: str, now: datetime):
    """识别时间表达。返回 (when, cleaned_text, explicit)。

    只认第一处时间表达:一句话里出现两个时间点时(「昨天买衣服,明天买菜」)应当在
    上层按标点拆成两句,而不是在这里猜该用哪个。
    """
    when, explicit, cleaned = None, False, text

    def _take(m, dt, why="time"):
        nonlocal when, explicit, cleaned
        if when is None:
            when, explicit = dt, True
            cleaned = cleaned[:m.start()] + " " + cleaned[m.end():]

    # 1) 绝对日期 2026-11-05 / 2026年11月5日
    m = re.search(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?", cleaned)
    if m:
        try:
            _take(m, datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                              now.hour, now.minute))
        except ValueError:
            pass

    # 2) 月份偏移(可带"5号"):上个月 / 下个月 5 号
    if when is None:
        m = re.search(rf"({_MONTH_ALT})\s*(?:份)?\s*(?:(\d{{1,2}}|[二三四五六七八九十]{{1,3}})\s*[号日])?",
                      cleaned)
        if m:
            offset = next(o for w, o in _MONTH_WORDS if w == m.group(1))
            day = None
            if m.group(2):
                day = int(m.group(2)) if m.group(2).isdigit() else int(cn_to_number(m.group(2)) or 0)
                day = day if 1 <= day <= 31 else None
            _take(m, _shift_month(now, offset, day))

    # 3) N 月 / N 月 M 号(不带"上/下"修饰)
    if when is None:
        m = re.search(rf"(\d{{1,2}}|{_CN_NUM})\s*月份?\s*(?:(\d{{1,2}}|{_CN_NUM})\s*[号日])?",
                      cleaned)
        if m:
            mon = int(m.group(1)) if m.group(1).isdigit() else int(cn_to_number(m.group(1)) or 0)
            if 1 <= mon <= 12:
                day = None
                if m.group(2):
                    day = int(m.group(2)) if m.group(2).isdigit() else int(cn_to_number(m.group(2)) or 0)
                    day = day if 1 <= day <= 31 else None
                # 没写年份的「9 月」在 11 月说出来,指的就是今年 9 月(已过去);
                # 「12 月」在 11 月说,只可能是去年 12 月 —— 未来请说「下个月」。
                year = now.year - 1 if mon > now.month else now.year
                dd = min(day or now.day, _month_days(year, mon))
                _take(m, datetime(year, mon, dd, now.hour, now.minute))

    # 4) 今天/昨天/前天/大前天/明天/后天/大后天
    if when is None:
        for word, delta in _DAY_WORDS:
            idx = cleaned.find(word)
            if idx >= 0:
                dt = now + timedelta(days=delta)
                when, explicit = dt, True
                cleaned = cleaned[:idx] + " " + cleaned[idx + len(word):]
                break

    # 5) N 天前 / N 天后 / N 日之内
    if when is None:
        m = re.search(rf"(\d{{1,3}}|{_CN_NUM})\s*天\s*(前|后)", cleaned)
        if m:
            n = int(m.group(1)) if m.group(1).isdigit() else int(cn_to_number(m.group(1)) or 0)
            delta = -n if m.group(2) == "前" else n
            _take(m, now + timedelta(days=delta))

    # 6) 上周/这周/下周
    if when is None:
        for word, delta in _WEEK_WORDS:
            idx = cleaned.find(word)
            if idx >= 0:
                when, explicit = now + timedelta(days=delta), True
                cleaned = cleaned[:idx] + " " + cleaned[idx + len(word):]
                break

    # 7) 光秃秃的「5 号」:没过 → 本月;已过 → 上个月(口语里 5 号说的都是已经发生的事)
    if when is None:
        m = re.search(rf"(\d{{1,2}}|{_CN_NUM})\s*[号日]", cleaned)
        if m:
            d = int(m.group(1)) if m.group(1).isdigit() else int(cn_to_number(m.group(1)) or 0)
            if 1 <= d <= 31:
                if d <= now.day:
                    dt = _shift_month(now, 0, d)
                else:
                    dt = _shift_month(now, -1, d)
                _take(m, dt)

    return (when or now), cleaned, explicit


# ══════════════════════════════════════════════════════════
# 金额
# ══════════════════════════════════════════════════════════
def _to_float(num: str) -> Optional[float]:
    try:
        return float(num.replace(",", ""))
    except ValueError:
        return None


def _extract_amount(text: str):
    """找金额。返回 (金额, 币种, [已匹配片段...], 是否带单位, 备注) 或 None。

    优先级:符号 > 数字+币种 > 数字+量级 > 中文数字+币种/量级 > 动词后的数字 > 唯一的裸数字。
    越往后越不确定,备注里会写明,让用户知道系统在猜。
    """
    # 1) 符号前置:$105、¥1,050、€30
    m = re.search(r"([¥￥$€£₩])\s*(\d[\d,]*(?:\.\d+)?)", text)
    if m:
        v = _to_float(m.group(2))
        if v is not None:
            return v, _SYMBOLS[m.group(1)], [m.group(0)], True, ""

    # 2) 数字 + 币种(105 块 / 105 美元 / 105 rmb)
    m = re.search(rf"(\d[\d,]*(?:\.\d+)?)\s*({_UNIT_ALT})", text, re.I)
    if m:
        v = _to_float(m.group(1))
        if v is not None:
            return v, CURRENCY_ALIASES[m.group(2).lower() if m.group(2).isascii()
                                       else m.group(2)], [m.group(0)], True, ""

    # 2b) 币种前置:美元 105 / rmb 88
    m = re.search(rf"({_UNIT_ALT})\s*(\d[\d,]*(?:\.\d+)?)", text, re.I)
    if m:
        v = _to_float(m.group(2))
        if v is not None:
            key = m.group(1).lower() if m.group(1).isascii() else m.group(1)
            return v, CURRENCY_ALIASES[key], [m.group(0)], True, ""

    # 3) 数字 + 量级(1.5k / 2w / 3 万)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(千|[kK]|[wW]|万)", text)
    if m:
        v = _to_float(m.group(1))
        if v is not None:
            scale = _SCALE.get(m.group(2).lower(), 1000)
            return v * scale, "CNY", [m.group(0)], False, "按人民币处理(只给了量级)"

    # 4) 中文数字 + 币种(一百零五块 / 两千美元)
    m = re.search(rf"({_CN_NUM})\s*({_UNIT_ALT})", text, re.I)
    if m:
        v = cn_to_number(m.group(1))
        if v is not None:
            return v, CURRENCY_ALIASES[m.group(2).lower() if m.group(2).isascii()
                                       else m.group(2)], [m.group(0)], True, ""

    # 5) 中文数字整串(两千三 / 三千五 / 两万)。
    #    注意必须**整串**交给 cn_to_number:先按 (千|万) 切会得到 "两"+"千" = 2000,
    #    而用户说的是 2300。
    m = re.search(rf"({_CN_NUM})", text)
    if m and len(m.group(1)) >= 2:
        v = cn_to_number(m.group(1))
        if v is not None:
            return v, "CNY", [m.group(0)], False, "按人民币处理(只给了量级)"

    # 6) 动词旁边的裸数字(花了 105 / 付 30)
    m = re.search(rf"(?:{'|'.join(sorted(_EXPENSE_VERBS, key=len, reverse=True))})"
                  rf"\s*(\d[\d,]*(?:\.\d+)?)", text)
    if m:
        v = _to_float(m.group(1))
        if v is not None:
            return v, "CNY", [m.group(0)], False, ""
    m = re.search(rf"(?:{'|'.join(sorted(_EXPENSE_VERBS, key=len, reverse=True))})"
                  rf"\s*({_CN_NUM})", text)
    if m:
        v = cn_to_number(m.group(1))
        if v is not None:
            return v, "CNY", [m.group(0)], False, ""

    # 7) 裸数字:只有一个就用它;有多个取最大(「买 2 件衣服 105」→ 105)。
    #    口语报备省略「元」是常态,单数字不再提示 —— 每笔都挂一句
    #    「按人民币处理」纯属噪音,反而让回执显得不可靠。
    nums = [(v, mm.group(0)) for mm in re.finditer(r"\d[\d,]*(?:\.\d+)?", text)
            if (v := _to_float(mm.group(0))) is not None]
    if len(nums) == 1:
        return nums[0][0], "CNY", [nums[0][1]], False, ""
    if len(nums) > 1:
        v, raw = max(nums, key=lambda x: x[0])
        return v, "CNY", [raw], False, f"句子里有多个数字,按最大的 {v:g} 元记账,请核对"
    return None


# ══════════════════════════════════════════════════════════
# 分类 / 收支方向
# ══════════════════════════════════════════════════════════
def _classify(text: str) -> str:
    low = text.lower()
    for cat, words in _CATEGORY_RULES:
        if any(w in low for w in words):
            return cat
    return "misc"


def _direction(text: str) -> str:
    """收入还是支出。两者都出现时按支出(「报销到账后退了款」这种复合句交给 LLM 分析)。"""
    has_in = bool(_INCOME_PAT.search(text))
    has_out = bool(_VERB_PAT.search(text))
    if has_in and not has_out:
        return "income"
    return "expense"


# 描述清洗:剥掉金额、时间、功能动词与量词,只留下"买的是什么"。
# 保留词先占位 —— 「打车/洗车/加油/租房」里的动词本身就是消费名,
# 删掉会变成「车 25」这种读不懂的残句。
_KEEP_PHRASES = ("打车", "洗车", "加油", "吃饭", "租房", "房租", "停车", "话费", "液化气")
_STOP_WORDS = (
    "花了", "花费", "付了", "付款", "支付", "买了", "充值", "充了", "消费",
    "收到", "赚了", "退了", "报销了", "发了", "发工资", "进账", "退款", "退回",
    "一共", "总共", "合计", "大概", "差不多",
    "花", "买", "购", "付", "交", "缴", "扣", "订", "租", "退", "收",
    "在", "的", "了", "呢", "啊", "吧", "呀", "哦", "嗯", "要", "去", "再",
    "又", "就", "还", "和", "跟", "给", "把", "被", "我", "你", "他", "她", "它",
    "件", "个", "只", "双", "条", "袋", "瓶", "杯", "张", "台", "部", "本",
    "支", "盒", "箱", "次", "份", "碗", "串", "根", "桶", "套", "顿",
)


def _clean_description(text: str, spans: list) -> str:
    out = text
    for sp in spans:
        out = out.replace(sp, " ")
    # 保护动词性名词
    for i, w in enumerate(_KEEP_PHRASES):
        out = out.replace(w, f"\x00{i}\x00")
    for w in sorted(_STOP_WORDS, key=len, reverse=True):
        out = out.replace(w, " ")
    out = re.sub(r"[，,。；;、!?！?：:]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip(" .,:;，。；：")
    for i, w in enumerate(_KEEP_PHRASES):
        out = out.replace(f"\x00{i}\x00", w)
    return re.sub(r"\s+", " ", out).strip()[:40]


_SPLIT_PAT = re.compile(r"[，,；;\n]+|(?:然后|另外|还有|以及|再就是|接着)")


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
def parse(text: str, now: datetime | None = None) -> list:
    """把一段话解析成 0..N 笔钱。

    「今天买了一件衣服花了 105 块,还买了 45 块的大米」
    → [衣服 105 CNY 今天, 大米 45 CNY 今天]

    拆分规则:按标点/连接词切段,逐段解析;**时间可继承** —— 后一段没写时间时沿用
    前一段的时间(「今天…,还买了…」),这样用户不必每句都重复报时。
    """
    now = now or datetime.now()
    text = (text or "").strip()
    if not text:
        return []

    segments = [s.strip() for s in _SPLIT_PAT.split(text) if s and s.strip()]

    out, inherited_when = [], None
    for seg in segments:
        p = _parse_one(seg, now, inherited_when)
        if p is None:
            continue
        inherited_when = p.when
        out.append(p)

    # 拆段只解析出 ≤1 笔 → 换用整句再解析一次。中文口语常带逗号
    # (「今天,我买了件衣服,花了 105 块」),拆开会让「衣服」这个分类线索掉在
    # 没有金额的段里,整句解析才拿得到完整语境。
    if len(out) <= 1:
        whole = _parse_one(text, now, None)
        if whole is not None:
            return [whole]
    return out


def _parse_one(seg: str, now: datetime, inherited_when: Optional[datetime]):
    original = seg
    when, cleaned, explicit_time = _parse_when(seg, now)
    if not explicit_time and inherited_when is not None:
        when = inherited_when

    cleaned = _QUANT_PAT.sub(" ", cleaned)        # 「一件」「2 袋」不是金额
    got = _extract_amount(cleaned)
    if got is None:
        return None
    amount, currency, spans, has_unit, warn = got
    if amount <= 0 or amount > 1e9:
        return None

    direction = _direction(original)
    desc = _clean_description(cleaned, spans)
    category = "income" if direction == "income" else _classify(original)

    # 商户:「在星巴克买了…」
    ent = ""
    m = re.search(r"在\s*([\u4e00-\u9fa5A-Za-z0-9]{1,10}?)\s*(?:买|花|吃|订|充|消费|缴)", original)
    if m:
        ent = m.group(1)

    warnings = [warn] if warn else []
    conf = 0.45
    if has_unit:
        conf += 0.2
    if explicit_time:
        conf += 0.2
    if category != "misc":
        conf += 0.1
    if _VERB_PAT.search(original) or _INCOME_PAT.search(original):
        conf += 0.05

    return ParsedExpense(
        text=original, direction=direction, amount=amount, currency=currency,
        when=when, category=category,
        description=desc or CATEGORY_LABELS.get(category, ""),
        entity=ent or (desc[:12] if desc else CATEGORY_LABELS.get(category, "未注明")),
        confidence=min(conf, 0.98), warnings=warnings,
        planned=when.date() > now.date(),
    )


# 完成态付钱动词:「买了/花了/交了」说明钱**已经出去了**;
# 「房租 3000 占收入多少」里的「租」不算 —— 那句在讨论额度,不是在报账。
_SETTLED_SPEND = ("花了", "买了", "付了", "支付了", "消费了", "充值了",
                  "交了", "缴了", "扣了", "刷了", "转了", "付账")


def has_settled_spend(text: str) -> bool:
    """句子里有没有「已经花出去」的完成态动词。"""
    t = text or ""
    return any(w in t for w in _SETTLED_SPEND)


def has_payment_verb(text: str) -> bool:
    """有没有「花/买/付/交/缴/扣」这类明确的付钱动词。

    用途:区分「我买了件衣服 105」和「衣服预算 800」—— 后者带金额但不是已发生的钱,
    自动记账会把预算额记成一笔支出。
    """
    return bool(_VERB_PAT.search(text or ""))


def has_expense_signal(text: str) -> bool:
    """这句话像不像「花了钱」的报备 —— 主管路由用,决定要不要交给财务 Agent。

    判据:能解析出金额,并且有花费动词 / 货币单位 / 明确的消费类目。
    只要求「有金额」会把「今天几号」「3 个月计划」也甩给财务,所以三条要占一条。
    """
    t = (text or "").strip()
    if not t or len(t) > 120:
        return False
    if not parse(t):
        return False
    has_verb = bool(_VERB_PAT.search(t) or _INCOME_PAT.search(t))
    has_unit = any(k in t.lower() for k in CURRENCY_ALIASES) or any(s in t for s in _SYMBOLS)
    has_cat = _classify(t) != "misc"
    return has_verb or has_unit or has_cat
