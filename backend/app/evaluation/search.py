"""LLM 비교군이 쓰는 카탈로그 텍스트 검색 (BM25, 상품명 + 특징). 속성·IG 엔진과 무관하다."""

import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

K1, B = 1.5, 0.75
FEATURE_CHARS = 600


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


@dataclass
class SearchHit:
    id: str
    title: str
    price: float | None
    score: float


class CatalogSearch:
    """@docid eval.catalog-search
    @see docs/evaluation.md#2-비교하는-시스템
    """

    def __init__(self, docs: list[tuple[str, str, float | None, str]]):
        """docs: (id, 상품명, 가격, 검색할 텍스트)"""
        self.meta = [(d[0], d[1], d[2]) for d in docs]
        self.tfs = [Counter(tokenize(d[3])) for d in docs]
        self.lengths = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = sum(self.lengths) / max(len(self.lengths), 1)
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    @classmethod
    def from_db(cls, conn: sqlite3.Connection, category_id: str) -> "CatalogSearch":
        rows = conn.execute(
            "SELECT id, title, price, features FROM products WHERE category_id = ? ORDER BY id",
            (category_id,)).fetchall()
        docs = []
        for pid, title, price, features in rows:
            text = " ".join(json.loads(features or "[]"))[:FEATURE_CHARS]
            docs.append((pid, title, price, f"{title} {title} {text}"))  # 상품명에 가중치
        return cls(docs)

    def search(self, query: str, k: int = 30) -> list[SearchHit]:
        terms = [t for t in tokenize(query) if t in self.idf]
        scores = []
        for i, tf in enumerate(self.tfs):
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.lengths[i] / self.avg_len))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [SearchHit(*self.meta[i], score=s) for s, i in scores[:k]]
