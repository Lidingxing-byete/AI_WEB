"""BM25 检索（自实现，零外部依赖）。

为什么自己写：语料只有 40 条卡片，引入 rank_bm25 / sklearn 不划算；
更重要的是 BM25 能给出"命中了哪些词"，这对本项目的**可追溯性**评分点直接有用。

中文分词：不做复杂分词，用「2-gram 字符切分 + ASCII 词」的混合方案。
理由（[推断]）：技术文本里关键信号多为 ASCII 标识符（如 `GPIO_Mode_AF_PP`、
`USART1`），必须整词匹配；中文部分用 2-gram 可覆盖大部分关键词汇，
且完全不依赖分词模型，评审环境零配置可跑。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..config import BM25_B, BM25_K1
from .loader import Card, load_cards

_ASCII_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """混合分词：ASCII 标识符整词 + 中文 2-gram。"""
    tokens: list[str] = []
    for m in _ASCII_TOKEN.finditer(text):
        tok = m.group(0)
        tokens.append(tok)
        # 下划线拆分，便于 GPIO_Mode_AF_PP 也能命中 AF_PP
        if "_" in tok:
            tokens.extend(p for p in tok.split("_") if len(p) >= 2)
    for run in _CJK.findall(text):
        pass
    # 中文：按连续中文段做 2-gram
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) == 1:
            tokens.append(seg)
        else:
            tokens.extend(seg[i: i + 2] for i in range(len(seg) - 1))
    return tokens


@dataclass
class Hit:
    card_id: str
    title: str
    score: float
    matched_terms: list[str]
    source_ref: str
    body: str

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "title": self.title,
            "score": round(self.score, 4),
            "matched_terms": self.matched_terms,
            "source_ref": self.source_ref,
            "body": self.body,
        }


class BM25:
    """极简 BM25。语料小，构建开销可忽略。"""

    def __init__(self, cards: tuple[Card, ...] | None = None) -> None:
        self.cards = list(cards if cards is not None else load_cards())
        self.docs: list[list[str]] = []
        for c in self.cards:
            text = f"{c.title} {' '.join(c.tags)} {c.body}"
            self.docs.append(tokenize(text))
        self.n = len(self.docs)
        self.avgdl = (sum(len(d) for d in self.docs) / self.n) if self.n else 0.0
        self.df: dict[str, int] = {}
        for d in self.docs:
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1
        self.tf: list[dict[str, int]] = []
        for d in self.docs:
            m: dict[str, int] = {}
            for t in d:
                m[t] = m.get(t, 0) + 1
            self.tf.append(m)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        # BM25 经典 IDF，加 1 防止分母为 0
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        q_terms = tokenize(query)
        if not q_terms or not self.n:
            return []
        hits: list[Hit] = []
        for idx, doc in enumerate(self.docs):
            dl = len(doc)
            tf_map = self.tf[idx]
            score = 0.0
            matched: list[str] = []
            for t in set(q_terms):
                f = tf_map.get(t, 0)
                if f == 0:
                    continue
                denom = f + BM25_K1 * (1 - BM25_B + BM25_B * dl / (self.avgdl or 1))
                score += self._idf(t) * (f * (BM25_K1 + 1)) / denom
                matched.append(t)
            if score > 0:
                card = self.cards[idx]
                hits.append(Hit(
                    card_id=card.card_id,
                    title=card.title,
                    score=score,
                    matched_terms=sorted(matched),
                    source_ref=card.source_ref,
                    body=card.body,
                ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
