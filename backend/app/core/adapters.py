"""카탈로그(카테고리 정의 + 상품)를 IG 엔진의 입력으로 바꾼다.

엔진은 카탈로그를 모르고, 카탈로그는 엔진을 모른다. 둘을 잇는 곳은 여기뿐이다.

@see docs/ig-engine.md#ig-엔진
"""

from collections.abc import Sequence

from app.catalog.repository import Product
from app.catalog.spec import CategorySpec
from app.core.model import AttributeDef, Item

# 인기도 점수의 베이지안 평균 가중치: 리뷰가 이만큼은 있어야 자기 평점을 믿는다.
RATING_PRIOR_WEIGHT = 50


def popularity_scores(products: Sequence[Product]) -> list[float]:
    """베이지안 평균 평점. 리뷰가 적은 상품의 극단적인 평점을 전체 평균 쪽으로 당긴다.

    @see docs/ig-engine.md#4-추천과-한계-표시
    """
    rated = [p for p in products if p.average_rating is not None and p.rating_number]
    if not rated:
        return [0.0] * len(products)
    mean = sum(p.average_rating for p in rated) / len(rated)
    m = RATING_PRIOR_WEIGHT
    return [
        ((p.average_rating or mean) * (p.rating_number or 0) + mean * m) / ((p.rating_number or 0) + m)
        for p in products
    ]


def from_catalog(
    spec: CategorySpec, products: Sequence[Product]
) -> tuple[dict[str, AttributeDef], list[Item]]:
    """@docid core.from-catalog"""
    attributes = {}
    for a in spec.attributes:
        if a.is_open:
            # 자유값 속성(brand 등)은 카탈로그에 실제로 있는 값들이 값 목록이 된다.
            values = tuple(sorted({p.attributes[a.key] for p in products if a.key in p.attributes}))
        else:
            values = tuple(v.value for v in a.values)
        if len(values) >= 2:
            attributes[a.key] = AttributeDef(a.key, values, a.askable)

    scores = popularity_scores(products)
    items = [
        Item(p.id, {k: v for k, v in p.attributes.items() if k in attributes}, score)
        for p, score in zip(products, scores)
    ]
    return attributes, items
