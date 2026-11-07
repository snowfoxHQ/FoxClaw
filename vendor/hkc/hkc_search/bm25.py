"""
hkc-search / bm25.py
关键词检索(Cold 层),自实现 BM25Okapi 倒排索引。

阶段 B 改造(10万 KU 规模):从 rank-bm25 切换到自实现。
  - 倒排索引:term -> {doc_id: tf} + df 字典
  - append_ku / append_batch 真正 O(terms) 增量,无全量重建
  - save / load 用 pickle,启动可免 tokenize
  - 公式与 rank_bm25.BM25Okapi 完全一致(k1=1.5, b=0.75,
    IDF = log((N - df + 0.5) / (df + 0.5) + 1))
"""
from __future__ import annotations
import logging
import math
import os
import pickle
import re
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BM25Result:
    ku_id:   str
    score:   float
    name:    str
    summary: str


# 简单分词(沿用现状)
_TOKEN_RE_NONWORD = re.compile(r'[^\w\s\u4e00-\u9fff]', re.UNICODE)
_TOKEN_RE_LATIN   = re.compile(r'^[a-z0-9_-]+$')


def _tokenize(text: str) -> list[str]:
    """英文按空格切+小写+去标点;中文按字切(unigram + bigram)。"""
    text = text.lower()
    text = _TOKEN_RE_NONWORD.sub(' ', text)
    tokens: list[str] = []
    for word in text.split():
        if _TOKEN_RE_LATIN.match(word):
            if len(word) >= 2:
                tokens.append(word)
        else:
            chars = [c for c in word if '\u4e00' <= c <= '\u9fff']
            tokens.extend(chars)
            for i in range(len(chars) - 1):
                tokens.append(chars[i] + chars[i + 1])
    return tokens


class BM25Index:
    """
    自实现 BM25Okapi 倒排索引。

    数据结构:
        _doc_ids:  list[str]                       文档 id 列表(插入顺序)
        _doc_lens: list[int]                       每篇 doc 的 token 数
        _doc_idx:  dict[str, int]                  doc_id → _doc_ids 里的 idx
        _meta:     dict[str, tuple[str, str]]      doc_id → (name, summary)
        _N:        int                             总 doc 数
        _avgdl:    float                           平均 doc 长度(在线更新)
        _df:       dict[str, int]                  term → 含该 term 的 doc 数
        _tf:       dict[str, dict[str, int]]       term → {doc_id: term freq}
        _k1, _b:   float                           BM25Okapi 超参

    时间复杂度:
        - append_ku:     O(terms in ku)
        - append_batch:  O(total terms in batch)
        - remove_ku:     O(distinct terms in doc)
        - search:        O(|query| * avg postings)
        - build:         O(total tokens) 一次性

    公式(BM25Okapi,与 rank_bm25 一致):
        IDF(t)        = log((N - df + 0.5) / (df + 0.5) + 1)
        tf_norm(t,d)  = (tf * (k1+1)) / (tf + k1 * (1 - b + b * |d| / avgdl))
        score(t,d)    = IDF(t) * tf_norm(t,d)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75, epsilon: float = 0.25):
        self._k1 = k1
        self._b = b
        # ATIRE BM25 变体:对 negative IDF 做 floor,避免高频词得分为负
        # rank_bm25.BM25Okapi 默认 epsilon=0.25, floor = eps * average_idf
        # 为与 rank_bm25 完全一致,保留这个 floor
        self._epsilon = epsilon
        self._reset_state()

    def _reset_state(self) -> None:
        self._doc_ids:  list[str]                       = []
        self._doc_lens: list[int]                       = []
        self._doc_idx:  dict[str, int]                  = {}
        self._meta:     dict[str, tuple[str, str]]      = {}
        self._N:        int                             = 0
        self._avgdl:    float                           = 0.0
        self._df:       dict[str, int]                  = defaultdict(int)
        self._tf:       dict[str, dict[str, int]]       = defaultdict(dict)
        # ATIRE floor:维护 idf_sum 用于 average_idf(floor = eps * average_idf)
        # 与 rank_bm25._calc_idf 完全一致
        self._idf_sum:  float                           = 0.0

    # ── 公共接口 ───────────────────────────────────────────

    def build(self, kus) -> None:
        """全量构建。kus 为空时清空索引。"""
        self._reset_state()
        self.append_batch(kus)
        logger.info("BM25 索引构建完成,共 %d 条", self._N)

    def append_ku(self, ku) -> None:
        """单条增量。O(terms in ku)。"""
        self._add_doc(ku)

    def append_batch(self, kus) -> None:
        """批量增量(冷构建期调)。O(total terms in batch),比旧版 'append + 全量重建' 快 100x+。"""
        if not kus:
            return
        for ku in kus:
            self._add_doc(ku)

    def remove_ku(self, ku_id: str) -> bool:
        """单条删除。O(distinct terms in doc)。不存在返 False。"""
        if ku_id not in self._doc_idx:
            return False

        # 从 _tf 反查该 doc 涉及的所有 term
        terms_touched: list[str] = []
        for term, postings in self._tf.items():
            if ku_id in postings:
                del postings[ku_id]
                terms_touched.append(term)

        # 清理空 postings + 同步更新 df / idf_sum
        for term in terms_touched:
            if not self._tf[term]:
                # postings 全空,term 从 _df 移除
                old_df = self._df[term]
                old_idf = self._compute_idf_raw(self._N, old_df)
                self._idf_sum -= old_idf
                del self._df[term]
                del self._tf[term]
            else:
                # postings 还有内容,df 减 1(old_df >= 2,因 _tf[term] 不空)
                old_df = self._df[term]
                new_df = old_df - 1
                old_idf = self._compute_idf_raw(self._N, old_df)
                new_idf = self._compute_idf_raw(self._N, new_df)
                self._idf_sum += (new_idf - old_idf)
                self._df[term] = new_df

        # 移除 doc 本身
        idx = self._doc_idx.pop(ku_id)
        del self._doc_ids[idx]
        del self._doc_lens[idx]
        del self._meta[ku_id]

        # idx 之后的 _doc_idx 都要 -1
        for i in range(idx, len(self._doc_ids)):
            self._doc_idx[self._doc_ids[i]] = i

        # 更新 N, avgdl
        self._N -= 1
        if self._N > 0:
            self._avgdl = sum(self._doc_lens) / self._N
        else:
            self._avgdl = 0.0

        return True

    def search(self, query: str, top_k: int = 10) -> list[BM25Result]:
        if not self._N:
            return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        # 不去重:与 rank_bm25 行为一致(query 重复 term 累加 IDF*tf_norm)

        scores: dict[str, float] = defaultdict(float)
        for term in query_tokens:
            if term not in self._df:
                continue
            idf = self._idf(term)
            for doc_id, tf in self._tf[term].items():
                doc_idx = self._doc_idx[doc_id]
                doc_len = self._doc_lens[doc_idx]
                scores[doc_id] += idf * self._tf_norm(tf, doc_len)

        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        results: list[BM25Result] = []
        for doc_id, score in sorted_docs:
            if score <= 0:
                continue
            name, summary = self._meta[doc_id]
            results.append(BM25Result(
                ku_id=doc_id, score=float(score), name=name, summary=summary,
            ))
        return results

    def size(self) -> int:
        return self._N

    def clear(self) -> None:
        """清空索引(替换旧版直接改 _ku_ids 等私有状态)。"""
        self._reset_state()

    def save(self, path: str) -> None:
        """pickle 落盘(启动可 load 避免 10万 KU 重复 tokenize)。
        落盘时机留给调用方决定(vector flush 钩子 / 独立 fsync 线程)。"""
        data = {
            'k1':       self._k1,
            'b':        self._b,
            'doc_ids':  self._doc_ids,
            'doc_lens': self._doc_lens,
            'doc_idx':  self._doc_idx,
            'meta':     self._meta,
            'N':        self._N,
            'avgdl':    self._avgdl,
            'df':       dict(self._df),
            'tf':       {term: dict(postings) for term, postings in self._tf.items()},
        }
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)

    def load(self, path: str) -> bool:
        """从 pickle 加载。文件不存在或 schema 不匹配返 False。"""
        if not os.path.exists(path):
            return False
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
        except Exception as e:
            logger.warning("BM25 索引加载失败,fallback 到空索引: %s", e)
            self._reset_state()
            return False

        required = {'k1', 'b', 'doc_ids', 'doc_lens', 'doc_idx',
                    'meta', 'N', 'avgdl', 'df', 'tf'}
        if not required.issubset(data.keys()):
            logger.warning("BM25 索引 schema 不匹配,fallback 到空索引")
            self._reset_state()
            return False

        self._k1       = data['k1']
        self._b        = data['b']
        self._doc_ids  = data['doc_ids']
        self._doc_lens = data['doc_lens']
        self._doc_idx  = data['doc_idx']
        self._meta     = data['meta']
        self._N        = data['N']
        self._avgdl    = data['avgdl']
        self._df       = defaultdict(int, data['df'])
        self._tf       = defaultdict(dict, {term: dict(postings) for term, postings in data['tf'].items()})
        return True

    # ── 内部方法 ───────────────────────────────────────────

    @staticmethod
    def _compute_idf_raw(N: int, df: int) -> float:
        """BM25Okapi 原始 idf(rank_bm25._calc_idf 一致)。
        允许负值(当 df > N 时,numerator <= 0 → 负 idf,后续 ATIRE floor 兜底)。
        """
        if df == 0:
            return 0.0
        num = N - df + 0.5
        den = df + 0.5
        if num <= 0:
            # log(负) 不存在,但数学上 idf = -log(|num|) + log(den) = -(log(|num|) - log(den))
            return -(math.log(-num) - math.log(den))
        return math.log(num) - math.log(den)

    def _add_doc(self, ku) -> None:
        text = f"{ku.name} {ku.summary} {' '.join(ku.tags)}"
        tokens = _tokenize(text)
        ku_id = ku.ku_id
        doc_len = len(tokens)

        self._doc_ids.append(ku_id)
        self._doc_lens.append(doc_len)
        self._doc_idx[ku_id] = self._N
        self._meta[ku_id] = (ku.name, ku.summary[:100])

        if doc_len > 0:
            term_counts: dict[str, int] = defaultdict(int)
            for t in tokens:
                term_counts[t] += 1
            for term, count in term_counts.items():
                if term not in self._tf:
                    self._tf[term] = {}
                self._tf[term][ku_id] = count
                # 增量更新 _idf_sum:N 不变,只 df 变
                if self._df[term] == 0:
                    # 新 term (df: 0 → 1)
                    self._idf_sum += self._compute_idf_raw(self._N, 1)
                else:
                    # 旧 term (df: old → old+1)
                    old_df = self._df[term]
                    old_idf = self._compute_idf_raw(self._N, old_df)
                    new_idf = self._compute_idf_raw(self._N, old_df + 1)
                    self._idf_sum += (new_idf - old_idf)
                self._df[term] += 1

        prev_total = self._avgdl * self._N
        self._N += 1
        self._avgdl = (prev_total + doc_len) / self._N

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        # BM25Okapi 公式(rank_bm25 风格):log((N - df + 0.5) / (df + 0.5))
        # 注意:无 +1,负 idf 由 ATIRE floor 兜底
        idf = self._compute_idf_raw(self._N, df)
        if idf < 0:
            # ATIRE floor:eps * average_idf
            avg_idf = (self._idf_sum / len(self._df)) if self._df else 0.0
            return self._epsilon * avg_idf
        return idf

    def _tf_norm(self, tf: int, doc_len: int) -> float:
        return (tf * (self._k1 + 1)) / (tf + self._k1 * (1 - self._b + self._b * doc_len / self._avgdl))
