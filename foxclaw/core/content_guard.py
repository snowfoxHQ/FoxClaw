"""内容安全闸门 (ContentGuard):合规红线的确定性拦截,输入与输出两端。

为什么要有这一层
────────────────
KERNEL_PROMPT 的【五、安全】管的是「指令 vs 数据」(别被外部内容骗),behavior.yaml 的
confirm_* 管的是「别乱动高危动作」。两者都不管「这话题本身不能碰」:模型仍可能被诱导
生成违规内容,或用户直接索要。提示词只能降低概率,对外交付的产品需要可复现的兜底。

所以这里只做确定性判定:**不做语义理解、不调模型**。命中就按固定话术切断,
不解释、不延伸、不反问 —— 拒绝本身不能变成信息泄漏或继续纠缠的入口。

三层词表来源
────────────
1. 内置规则(本文件 `_BUILTIN_RULES`):只放**请求型教唆**的高置信度模式 ——「怎么制作
   炸药」「教我自杀方法」这类。措辞窄,误伤率极低,开箱即用。
2. 外部词表(`FOXCLAW_CONTENT_LEXICON`,逗号分隔多个路径):一行一词,`#` 注释,
   可选 `词<TAB>类别`。平台审核要求的大类词表(政治/色情/暴力)体量在数万条量级,
   硬编码进代码既不现实也无法随监管口径更新,交由部署方挂载。
3. 自定义类别:外部词表里的类别名会原样出现在命中结果里,便于审计归类。

为什么不内置大词表
────────────────
六万条量级的词表进仓库,一是体积与构建成本,二是监管口径变化时改代码而非改配置。
更重要的是:知识库导入、文档解析属于**用户自己的资料**,不在这里过滤 —— 过滤器只挂在
「用户请求」与「模型输出」两端,避免把历史书、法律条文、医学教材的内容当成违规请求。

三档模式 (FOXCLAW_CONTENT_GUARD_MODE)
────────────────────────────────────
  enforce  真正拦截:输入命中直接回固定话术、不进模型;输出命中整段替换。
  audit    只记录不拦截(默认之外的灰度档,用于观察误伤率)。
  off      完全关闭。

默认 enforce:这一层是给对外产品兜底的,默认不生效等于没做。内置规则范围窄,
所以默认强制不会误伤日常对话;是否挂平台大类词表由 `FOXCLAW_CONTENT_LEXICON` 决定。

归一化对抗
──────────
全角、零宽字符、空格插字、大小写是最常见的绕过手法,扫描前统一归一化,
并对「去掉空白标点后的紧凑串」再扫一遍。拼音/谐音/拆字改写不在确定性层面处理 ——
那是提示词与人工审核的职责,这里明确只做限速带,不做墙。
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MODE_ENV = "FOXCLAW_CONTENT_GUARD_MODE"
LEXICON_ENV = "FOXCLAW_CONTENT_LEXICON"
VALID_MODES = ("off", "audit", "enforce")

REFUSAL_ZH = "我们换个话题聊聊吧。"
REFUSAL_EN = "Let's talk about something else."

# 内置规则只覆盖「请求型教唆」:有明确动作词 + 明确对象,措辞窄、误伤低。
_BUILTIN_RULES: dict[str, tuple[str, ...]] = {
    "illegal_solicit": (
        r"(?:怎么|如何|教我|教我如何|帮我|求|来个|写个|写一个).{0,12}"
        r"(?:制作|合成|提炼|配制|自制).{0,8}(?:毒品|冰毒|海洛因|摇头丸|炸弹|炸药|雷管|枪支)",
        r"(?:毒品|冰毒|海洛因|炸弹|炸药).{0,8}(?:制作|合成|提炼|配方|教程|方法)",
        r"(?:洗钱|跑分|杀猪盘|电信诈骗).{0,8}(?:教程|方法|流程|怎么|如何|话术)",
        # 负向前瞻:排除「如何防止/避免入侵」这类防御性提问,那是正常需求。
        r"(?:怎么|如何|教我|帮我|求)(?!.{0,6}(?:防止|避免|防范|抵御|修复)).{0,12}"
        r"(?:入侵|攻击|破解).{0,10}(?:别人|他人|网站|服务器|系统|账号)",
        r"(?:伪造|办假).{0,6}(?:身份证|护照|证件|发票|公章)",
    ),
    "self_harm": (
        r"(?:怎么|如何|教我|最好的方式|最不痛|无痛).{0,10}(?:自杀|轻生|结束生命)",
        r"(?:自杀|自残|割腕).{0,8}(?:方法|教程|最不痛|无痛|怎么才)",
    ),
    "minor_sexual": (
        r"(?:未成年|儿童|幼女|幼童|萝莉).{0,8}(?:色情|裸照|裸体|性行为|做爱|性交)",
        r"(?:色情|裸照).{0,8}(?:未成年|儿童|幼女)",
    ),
}

_BUILTIN_COMPILED = {cat: [re.compile(p, re.IGNORECASE) for p in pats]
                     for cat, pats in _BUILTIN_RULES.items()}

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
# 紧凑串去掉的字符:空白 + 常见中英标点,用来对抗「敏 感 词」「敏*感*词」这类插字绕过。
_STRIP_RX = re.compile(r"[\s\u3000·・\-_*~,.;:!?/\\|'\"`()\[\]{}<>@#$%^&+=、。，；：！？…—～《》“”‘’（）【】]")


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def refusal_for(text: str) -> str:
    """固定话术:跟随输入语种,只有中英两种 —— 拒绝语本身不该携带任何额外信息。"""
    return REFUSAL_ZH if _has_cjk(text) else REFUSAL_EN


def normalize(text: str) -> str:
    """NFKC(全半角/兼容字符)+ 去零宽 + 小写。"""
    s = unicodedata.normalize("NFKC", str(text or "")).translate(_ZERO_WIDTH)
    return s.lower()


def compact(text: str) -> str:
    """归一化后再去掉空白与标点,用于抗插字绕过。"""
    return _STRIP_RX.sub("", normalize(text))


@dataclass(frozen=True)
class Hit:
    category: str
    word: str
    source: str  # builtin / lexicon

    def to_dict(self) -> dict:
        return {"category": self.category, "word": self.word, "source": self.source}


class _Trie:
    """Aho-Corasick:一次性建表,之后线性扫描。

    词表可能上万条,逐词 `in text` 是不可接受的;纯 Python 实现避免为此引入新依赖。
    """

    __slots__ = ("goto", "fail", "out")

    def __init__(self, entries: Iterable[tuple[str, str, str]]):
        self.goto: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[list[tuple[str, str, str]]] = [[]]
        for word, cat, src in entries:
            self._add(word, cat, src)
        self._build()

    def _add(self, word: str, cat: str, src: str) -> None:
        node = 0
        for ch in word:
            nxt = self.goto[node].get(ch)
            if nxt is None:
                nxt = len(self.goto)
                self.goto[node][ch] = nxt
                self.goto.append({})
                self.fail.append(0)
                self.out.append([])
            node = nxt
        self.out[node].append((word, cat, src))

    def _build(self) -> None:
        queue = list(self.goto[0].values())
        for n in queue:
            self.fail[n] = 0
        head = 0
        while head < len(queue):
            node = queue[head]
            head += 1
            for ch, nxt in self.goto[node].items():
                queue.append(nxt)
                f = self.fail[node]
                while f and ch not in self.goto[f]:
                    f = self.fail[f]
                self.fail[nxt] = self.goto[f].get(ch, 0)
                self.out[nxt].extend(self.out[self.fail[nxt]])

    def find(self, text: str) -> list[tuple[str, str, str]]:
        node, found = 0, []
        for ch in text:
            while node and ch not in self.goto[node]:
                node = self.fail[node]
            node = self.goto[node].get(ch, 0)
            if self.out[node]:
                found.extend(self.out[node])
        return found


def load_lexicon(paths: Iterable[str | Path]) -> list[tuple[str, str, str]]:
    """读取外部词表。一行一词:`词` 或 `词<TAB>类别`;`#` 开头为注释。

    读不到或格式不对的词直接跳过 —— 词表是部署方维护的外部输入,
    不能因为一个坏行让整个守卫启动失败(那样只会被关掉)。
    """
    entries: list[tuple[str, str, str]] = []
    for raw in paths:
        p = Path(raw).expanduser()
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            word, _, cat = s.partition("\t")
            word = compact(word)
            if len(word) < 2:
                continue
            entries.append((word, (cat.strip() or "platform"), "lexicon"))
    return entries


@dataclass
class Decision:
    blocked: bool
    mode: str
    hit: Hit | None = None
    text: str = ""

    def to_dict(self) -> dict:
        return {"blocked": self.blocked, "mode": self.mode,
                "hit": self.hit.to_dict() if self.hit else None}


class ContentGuard:
    """输入/输出两端的内容安全闸门。无状态判定 + 可挂载,与 SensitiveWritePolicy 同构。"""

    def __init__(self, mode: str | None = None, lexicon_paths: Iterable[str] | None = None,
                 builtin: bool = True):
        self.mode = self._resolve_mode(mode)
        self._builtin = _BUILTIN_COMPILED if builtin else {}
        entries = load_lexicon(lexicon_paths if lexicon_paths is not None
                               else self._env_lexicons())
        self._trie = _Trie(entries) if entries else None
        self.lexicon_size = len(entries)

    @staticmethod
    def _resolve_mode(mode: str | None = None) -> str:
        m = (mode or os.environ.get(MODE_ENV, "") or "enforce").strip().lower()
        return m if m in VALID_MODES else "enforce"

    @staticmethod
    def _env_lexicons() -> list[str]:
        raw = os.environ.get(LEXICON_ENV, "").strip()
        return [x.strip() for x in raw.split(",") if x.strip()]

    def enabled(self) -> bool:
        return self.mode != "off"

    def scan(self, text: str) -> tuple[Hit, ...]:
        """返回命中项(去重,保序)。文本为空或未启用时返回空。"""
        if not self.enabled() or not text:
            return ()
        hits: list[Hit] = []
        seen: set = set()

        def _push(word: str, cat: str, src: str) -> None:
            key = (cat, word)
            if key not in seen:
                seen.add(key)
                hits.append(Hit(cat, word, src))

        # 原文 + 紧凑串两遍:原文保住命中片段可读,紧凑串对付全角与插字绕过。
        variants = (text, compact(text))
        for cat, patterns in self._builtin.items():
            for rx in patterns:
                for v in variants:
                    m = rx.search(v)
                    if m:
                        _push(m.group(0), cat, "builtin")
                        break
                else:
                    continue
                break

        if self._trie is not None:
            for word, cat, src in self._trie.find(compact(text)):
                _push(word, cat, src)
        return tuple(hits)

    def check(self, text: str) -> Decision:
        """判定一段文本。enforce 命中 → blocked=True;audit 只记录。"""
        hits = self.scan(text)
        if not hits:
            return Decision(False, self.mode)
        hit = hits[0]
        if self.mode == "enforce":
            return Decision(True, self.mode, hit, refusal_for(text))
        print(f"[Foxclaw] ⚠ 内容命中(未拦截): category={hit.category} word={hit.word!r}")
        return Decision(False, self.mode, hit)

    def guard_input(self, text: str) -> Decision:
        return self.check(text)

    def sanitize_output(self, text: str) -> Decision:
        """输出端:命中则整段替换为固定话术(不保留任何原文片段)。"""
        d = self.check(text)
        if d.blocked:
            d.text = refusal_for(text)
        return d


class StreamFilter:
    """流式输出的增量过滤。

    词可能跨 chunk,所以保留尾部 `window` 字符不立即下发,命中即中止整段输出。
    这只能保证「命中点之后不再吐字」,已经流出去的片段收不回 —— 流式的固有代价,
    换来的是不在每个 token 上做全量重扫。
    """

    def __init__(self, guard: ContentGuard, window: int = 24):
        self.guard = guard
        self.window = max(4, int(window))
        self.buf = ""
        self.tripped = False
        self.decision: Decision | None = None

    def feed(self, chunk: str) -> str:
        """喂一个增量,返回可以安全下发的部分。命中后恒返回空串。"""
        if not self.guard.enabled():
            return chunk
        if self.tripped:
            return ""
        self.buf += chunk or ""
        d = self.guard.check(self.buf)
        if d.blocked:
            self.tripped = True
            self.decision = d
            self.buf = ""
            return ""
        if len(self.buf) <= self.window:
            return ""
        out, self.buf = self.buf[:-self.window], self.buf[-self.window:]
        return out

    def finish(self) -> str:
        """收尾:吐出缓冲;若已命中则改用固定话术。"""
        if self.tripped:
            return self.decision.text if self.decision else REFUSAL_ZH
        tail, self.buf = self.buf, ""
        return tail


def attach(router, guard: ContentGuard | None = None):
    """挂到 ModelRouter(LLM 唯一咽喉),输出端过滤对所有 Agent 一次性生效。"""
    router.content_guard = guard or ContentGuard()
    return router.content_guard


def guard_input(router, text: str) -> Decision:
    """对话入口的统一闸门:未挂守卫时一律放行(向后兼容)。"""
    g = getattr(router, "content_guard", None)
    if g is None:
        return Decision(False, "off")
    return g.guard_input(text)
