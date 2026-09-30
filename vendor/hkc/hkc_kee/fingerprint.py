"""
hkc-kee / fingerprint.py
知识规范指纹 (Canonical Fingerprint)

与 Crystallizer 的 light fingerprint 分工:

  层            指纹类型             视角        用途
  ─────────────────────────────────────────────────────────
  Crystallizer  light(结构哈希)     事件视角    粗去重标签,不做决策
  KEE           canonical(语义规范)  知识视角    知识身份判定,去重权威

canonical fingerprint 回答的问题是:
  "这两个 KU 在知识层面是不是同一个东西?"

它基于 KU 的**知识身份要素**(类型 + 规范化名称 + 领域),而非原始字节。
所以"价值投资"和"价值投资 "(尾空格)、"Value Investing"的不同记录,
只要规范化后语义身份相同,canonical fingerprint 就相同 → 判为同一知识。

注:这里**有意**只做规则化规范(normalize)而非向量语义 —— 它是**又快又准的 exact 快路径**,
覆盖"同一知识的不同来源重复录入"这一主要去重场景。
向量级语义归并(同义词/近义表达,如 AI↔人工智能)**已经实现**,位于 dedup.py 的语义近重层:
exact 指纹未命中时才回落到它(opt-in:HKC_KEE_SEMANTIC=1,仅身份型 Entity/Concept)。
两者是分工,不是替代 —— 别把向量塞进指纹,那会让每次查重都要编码。
"""
from __future__ import annotations
import hashlib
import unicodedata


def normalize_name(name: str) -> str:
    """
    规范化知识名称,用于身份比对。

    步骤:Unicode NFKC 归一 → 转小写 → 仅保留字母与数字(去除所有标点、
    空白、符号、连字符等)→ strip。
    目的:让"价值投资"、"价值投资 "、"价值-投资"、"Value Investing"
    规范化后落到同一规范形。

    用 Unicode 类别过滤(保留 L* 字母 / N* 数字),比手写标点字符类更稳健,
    天然覆盖中英文标点、全角半角、各种连字符。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).lower()
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "N"):   # Letter / Number
            out.append(ch)
    return "".join(out)


def canonical_fingerprint(
    ku_type: str,
    name: str,
    domain: str = "",
) -> str:
    """
    计算 KU 的规范指纹。

    身份要素:类型 + 规范化名称 + 规范化领域。
    同类型、同规范名、同领域 → 同指纹 → 知识层面视为同一 KU。

    返回:sha256 十六进制(前 24 位,碰撞概率可忽略)。
    """
    parts = [
        (ku_type or "").strip().lower(),
        normalize_name(name),
        normalize_name(domain),
    ]
    key = "\x1f".join(parts)   # 用不可见分隔符避免拼接歧义
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def fingerprint_of_ku(ku) -> str:
    """从一个 KU 对象算 canonical fingerprint。"""
    ku_type = getattr(getattr(ku, "ku_type", None), "value", None) or str(getattr(ku, "ku_type", ""))
    name = getattr(ku, "name", "") or getattr(ku, "title", "")
    domain = getattr(ku, "domain", "") or ""
    return canonical_fingerprint(ku_type, name, domain)
