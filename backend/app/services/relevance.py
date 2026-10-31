"""텍스트 관련도: 조건이 똑같이 맞는 상품 사이를 대화와 가까운 순으로 정렬한다.

질문은 카테고리 정의의 속성으로만 고르지만, 같은 조건을 만족하는 상품이 수십~수백 개씩 남는다.
그 안의 순서를 정할 때는 속성 밖의 정보(상품명, 특징, 세부 정보 전체)를 쓴다.
- BM25: 대화에서 뽑은 영어 키워드(메모리 keywords)로 상품 글을 찾는다.
- 임베딩: 대화 요구 글(근거 발화 + 맥락 + 키워드, 언어 무관)과 상품 글의 코사인 유사도.
두 순위를 RRF(Reciprocal Rank Fusion)로 합친다. 임베딩이 없거나 실패하면 BM25만 쓴다.

@see docs/briefing.md#4-아키텍처와-코드-지도
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.catalog.search import CatalogSearch
from app.config import settings
from app.llm import LLMClient

logger = logging.getLogger(__name__)

RRF_K = 60
MAX_TEXT_CHARS = 6000
# 순서·재고 정보라 상품 성격과 무관한 세부 정보
SKIP_DETAILS = {"Best Sellers Rank", "Date First Available", "Is Discontinued By Manufacturer",
                "Customer Reviews", "ASIN"}

# 글 → 임베딩 벡터 (실패하면 예외)
Embedder = Callable[[str], list[float]]


def embedder_for(client: LLMClient) -> Embedder:
    """설정의 임베딩 모델로 글 하나를 임베딩하는 함수 (상품 임베딩과 같은 모델·차원)."""
    model, dims = settings.openai_embedding_model, settings.openai_embedding_dimensions
    return lambda text: client.embed([text], model=model, dimensions=dims)[0]


def product_text(title: str, features: str | None, details: str | None) -> str:
    """상품 하나의 검색용 글: 상품명, 특징, 세부 정보 (DB의 JSON 문자열을 받는다).

    @see docs/ig-engine.md#4-추천과-한계-표시
    """
    feats = " ".join(json.loads(features or "[]"))
    det = json.loads(details or "{}")
    det_text = "; ".join(f"{k}: {v}" for k, v in det.items() if k not in SKIP_DETAILS)
    return f"{title}\n{feats}\n{det_text}"[:MAX_TEXT_CHARS]


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


@dataclass
class TextIndex:
    """카테고리 하나의 상품 글 색인."""

    ids: list[str]
    bm25: CatalogSearch
    vectors: np.ndarray | None = None  # (상품 수, 차원), 정규화됨. 임베딩이 없는 상품은 0 벡터
    model: str | None = None
    dimensions: int | None = None

    @classmethod
    def from_texts(cls, texts: dict[str, str], vectors: dict[str, list[float]] | None = None,
                   model: str | None = None, dimensions: int | None = None) -> "TextIndex":
        ids = sorted(texts)
        titles = {i: texts[i].split("\n", 1)[0] for i in ids}
        # 상품명에 가중치 (글 앞에 상품명이 한 번 더 들어가게, CatalogSearch.from_db와 같은 방식)
        bm25 = CatalogSearch([(i, titles[i], None, f"{titles[i]} {texts[i]}") for i in ids])
        matrix = None
        if vectors:
            dims = len(next(iter(vectors.values())))
            matrix = np.zeros((len(ids), dims), dtype=np.float32)
            for n, i in enumerate(ids):
                if i in vectors:
                    v = np.asarray(vectors[i], dtype=np.float32)
                    matrix[n] = v / (np.linalg.norm(v) or 1.0)
        return cls(ids, bm25, matrix, model, dimensions)


def read_product_texts(conn: sqlite3.Connection, category_id: str) -> dict[str, str]:
    rows = conn.execute("SELECT id, title, features, details FROM products WHERE category_id = ?",
                        (category_id,)).fetchall()
    return {r[0]: product_text(r[1], r[2], r[3]) for r in rows}


def load_text_index(conn: sqlite3.Connection, category_id: str) -> TextIndex:
    """DB에서 색인을 만든다. 카탈로그·임베딩이 바뀌지 않았으면 캐시를 쓴다(요청마다 다시 만들지 않음).

    @see docs/ig-engine.md#4-추천과-한계-표시
    """
    row = conn.execute(
        "SELECT c.updated_at, COUNT(e.product_id) FROM categories c "
        "LEFT JOIN products p ON p.category_id = c.id "
        "LEFT JOIN product_embeddings e ON e.product_id = p.id WHERE c.id = ?", (category_id,)).fetchone()
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    return _cached_index(db_file, category_id, row[0] if row else None, row[1] if row else 0)


@lru_cache(maxsize=8)
def _cached_index(db_file: str, category_id: str, updated_at: str | None, embedded: int) -> TextIndex:
    conn = sqlite3.connect(db_file)
    try:
        texts = read_product_texts(conn, category_id)
        vectors, model, dims = {}, None, None
        for pid, model, dims, blob in conn.execute(
                "SELECT e.product_id, e.model, e.dimensions, e.vector FROM product_embeddings e "
                "JOIN products p ON p.id = e.product_id WHERE p.category_id = ?", (category_id,)):
            vectors[pid] = np.frombuffer(blob, dtype=np.float32)
    finally:
        conn.close()
    return TextIndex.from_texts(texts, vectors or None, model, dims)


def relevance_scores(index: TextIndex, keywords: str, context: str,
                     embed: Embedder | None = None) -> dict[str, float]:
    """상품 id → RRF 점수 (클수록 대화와 가깝다). 근거가 없으면 빈 dict.

    @docid chat.text-relevance
    @see docs/architecture.md#1-한눈에-보기
    @see docs/briefing.md#ig-엔진-ig-enginemd
    @see docs/ig-engine.md#4-추천과-한계-표시
    """
    rankings: list[list[str]] = []
    if keywords.strip():
        rankings.append([h.id for h in index.bm25.search(keywords, k=len(index.ids))])
    if embed is not None and index.vectors is not None and context.strip():
        try:
            query = np.asarray(embed(context), dtype=np.float32)
            sims = index.vectors @ (query / (np.linalg.norm(query) or 1.0))
            rankings.append([index.ids[i] for i in np.argsort(-sims)])
        except Exception as e:  # 임베딩 실패는 BM25만으로 계속한다
            logger.warning("query embedding failed, using BM25 only: %s", e)
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, pid in enumerate(ranking, 1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank)
    return scores
