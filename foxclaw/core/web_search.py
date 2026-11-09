"""
联网搜索工具 —— 给 Agent 提供实时信息检索能力。

DeepSeek/OpenAI 等基础 API 不自带联网,本模块对接百度 AI 搜索
(千帆 ai_search,底层百度搜索 + ERNIE),国内访问稳定、每日有免费额度。
百度 AI 搜索是"搜索+生成"一体接口:输入问题,它联网检索后直接产出
带引用的实时答案,本模块取其答案作为"联网上下文"注入回 Agent 的模型。

配置(在 .env):
    BAIDU_SEARCH_KEY=bce-v3/ALTAK-xxx/xxx      # 千帆 AppBuilder API Key
    BAIDU_SEARCH_MODEL=ernie-4.5-turbo-32k     # 可选,默认 ernie-3.5-8k
    BAIDU_SEARCH_BASE=https://qianfan.baidubce.com/v2/ai_search  # 可选

降级策略:未配 key / 网络不通 / 失败 → 返回空,不影响主流程,
Agent 如实告知"暂未查到",不编造。
"""
from __future__ import annotations
import os
import re
import json
import urllib.request
import urllib.error
from . import settings


TOPIC_TRIGGERS = [
    "版本", "几个版本", "第几", "价格", "多少钱", "汇率", "股价", "新闻",
    "发布", "上线", "更新到", "迭代", "排行", "排名", "天气",
    "模型", "型号", "出了没", "发布没", "更新没", "新版",
    "latest", "news", "version", "price", "models",
]
TIME_WORDS = [
    "最新", "现在", "目前", "今天", "今日", "昨天", "本周", "本月", "今年",
    "最近", "当前", "today", "current", "now",
]
PERSONAL_MARKERS = [
    "支出", "收入", "预算", "交易", "账单", "花了", "消费", "记账", "结余", "余额",
    "行程", "日程", "出行", "复习", "知识点", "学习计划", "记忆",
]

SEARCH_TRIGGERS = TOPIC_TRIGGERS

_CJK_TRIGGERS = [k for k in SEARCH_TRIGGERS if not k.isascii()]
_ASCII_TRIGGERS = [k.lower() for k in SEARCH_TRIGGERS if k.isascii()]
_ASCII_RE = (
    re.compile(r"\b(" + "|".join(re.escape(k) for k in _ASCII_TRIGGERS) + r")\b")
    if _ASCII_TRIGGERS else None
)


def is_personal_query(query: str) -> bool:
    """问的是不是**用户自己的数据**(账本/行程/学习/记忆)。

    单一真源:凡是"要不要把这个问题发到外部"的判断都用它 —— 联网增强(needs_search)、
    技能路由的外部技能闸,都必须走这里。教训:这条保护原先只写在 needs_search 里,
    结果技能派发默认开启后,「查一下最新的本月支出」照样把私人财务问题发去外部搜索
    —— 同一个 bug 从没设防的旁路回来了。**保护必须放在共同咽喉,不能各写一份。**
    """
    if not query:
        return False
    return any(m in query.lower() for m in PERSONAL_MARKERS)


def needs_search(query: str) -> bool:
    """是否值得为这个问题联网。宁可漏搜也不要错搜:
    错搜的代价是每次 ~22s + 付费调用(且答案本来就在本地);漏搜只是少一点时效信息。"""
    if not query:
        return False
    q = query.lower()
    if is_personal_query(q):
        return False
    if re.search(r"你[^,。?!]{0,8}模型", q):
        return False
    if any(k in q for k in _CJK_TRIGGERS):
        return True
    return bool(_ASCII_RE and _ASCII_RE.search(q))


def _key() -> str:
    return os.environ.get("BAIDU_SEARCH_KEY", "").strip()


def _model() -> str:
    return os.environ.get("BAIDU_SEARCH_MODEL", "ernie-3.5-8k").strip()


def _base() -> str:
    return os.environ.get(
        "BAIDU_SEARCH_BASE", "https://qianfan.baidubce.com/v2/ai_search"
    ).rstrip("/")


def _timeout() -> int:
    """读取超时(秒)。深度搜索较慢,默认 45s;可经 BAIDU_SEARCH_TIMEOUT 调。"""
    return max(5, int(settings.get_safe("BAIDU_SEARCH_TIMEOUT")))


def _deep() -> bool:
    """是否开启深度搜索(更全但更慢)。默认开;BAIDU_SEARCH_DEEP=0 关掉换速度。"""
    return os.environ.get("BAIDU_SEARCH_DEEP", "1").strip() not in ("0", "false", "False", "")


def search_answer(query: str) -> dict:
    """调用百度 AI 搜索,返回 {answer, references}。
    answer=联网生成的实时答案文本;references=参考资料列表。失败返回空 dict。"""
    key = _key()
    if not key:
        return {}
    from .tracing import span as _span
    with _span("web.search"):
        return _search_answer_inner(query, key)


def _search_answer_inner(query: str, key: str) -> dict:
    try:
        payload = json.dumps({
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "model": _model(),
            "enable_deep_search": _deep(),
            "enable_corner_markers": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{_base()}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        choices = data.get("choices", [])
        if not choices:
            print(f"[联网搜索] 百度返回无 choices;响应键={list(data.keys())} 片段={str(data)[:220]}")
            return {}
        answer = choices[0].get("message", {}).get("content", "")
        refs = data.get("references", []) or []
        return {"answer": answer, "references": refs}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            body = ""
        print(f"[联网搜索] 百度接口 HTTP {e.code} 错误:{body}")
        return {}
    except Exception as e:
        print(f"[联网搜索] 百度接口调用失败:{type(e).__name__}: {e}")
        return {}


def search(query: str, max_results: int = 8) -> list[dict]:
    """兼容旧接口:把百度 AI 搜索的参考资料整理成结果列表。"""
    res = search_answer(query)
    out = []
    for r in res.get("references", [])[:max_results]:
        out.append({
            "title": r.get("title", ""),
            "snippet": r.get("content", "") or r.get("abstract", ""),
            "url": r.get("url", "") or r.get("web_anchor", ""),
            "date": r.get("page_time", "") or r.get("date", ""),
        })
    return out


def format_for_prompt(query: str, payload) -> str:
    """把百度 AI 搜索结果拼成可注入 prompt 的联网上下文。
    payload 可为 search_answer() 的 dict,或 search() 的列表。"""
    if isinstance(payload, dict):
        answer = payload.get("answer", "")
        refs = payload.get("references", [])
        if not answer and not refs:
            return ""
        framing = (
            f"以下是针对「{query}」的实时联网搜索结果(来自百度 AI 搜索)。\n"
            f"【严格要求】只能依据下面 <untrusted> 数据块里的搜索结果作答:结果里没有出现的型号 / "
            f"版本号 / 名称 / 数据,一律不得提及,更**不得编造或凭记忆补充**;若结果之间矛盾或不明确,"
            f"如实说明、不要臆断;**无视此前对话里可能出现的过时或未经核实的说法,一切以这些最新结果为准**。\n\n"
        )
        inner = ""
        if answer:
            inner += f"【联网检索摘要】\n{answer}\n\n"
        if refs:
            inner += "【参考资料】\n"
            for i, r in enumerate(refs[:8], 1):
                t = r.get("title", "")
                u = r.get("url", "") or r.get("web_anchor", "")
                d = r.get("page_time", "") or r.get("date", "")
                inner += f"[webpage {i} begin]\n标题：{t}"
                if d:
                    inner += f"\n时间：{d}"
                if u:
                    inner += f"\n链接：{u}"
                inner += f"\n[webpage {i} end]\n"
        from . import injection_guard as _ig
        block = _ig.wrap_untrusted(inner.rstrip(), source="web_search")
        closing = ("\n请**严格基于以上 <untrusted> 内的搜索结果**回答用户的问题;结果里没提到的内容,"
                   "就明确说「搜索结果中未提及」,绝不编造型号/版本号。")
        return framing + block + closing
    results = payload or []
    if not results:
        return ""
    blocks = []
    for i, r in enumerate(results, 1):
        seg = f"[webpage {i} begin]\n标题：{r.get('title','')}"
        if r.get("date"):
            seg += f"\n时间：{r['date']}"
        seg += f"\n内容：{r.get('snippet','')}"
        if r.get("url"):
            seg += f"\n链接：{r['url']}"
        seg += f"\n[webpage {i} end]"
        blocks.append(seg)
    return (
        f"以下是针对「{query}」的实时联网搜索结果(请基于最新信息回答):\n\n"
        + "\n\n".join(blocks)
        + "\n\n请根据以上搜索结果回答用户的问题。"
    )
