"""목표 상품에 대한 확률분포(belief)를 관측으로부터 계산한다.

@see docs/ig-engine.md#ig-엔진
"""

import math
from collections.abc import Iterable, Mapping, Sequence

from app.core.model import AttributeDef, Item, Observation


def likelihood(item: Item, obs: Observation, attr: AttributeDef) -> float:
    """P(관측 | 목표 상품 = item).

    - 상품의 값이 관측 값과 같으면 c, 다르면 (1-c)/(k-1)
    - 상품의 값이 unknown이면 1/k (어떤 답에도 똑같이 반응 → 이 관측으로는 변하지 않음)

    c는 1/k 이상으로 올린다. 그보다 낮으면 일치하는 상품이 오히려 불리해진다.

    @docid core.likelihood
    @see docs/ig-engine.md#1-답변-가능도-모델
    """
    k = len(attr.values)
    value = item.attributes.get(obs.attribute)
    if value is None or value not in attr.values:
        return 1.0 / k
    c = max(obs.confidence, 1.0 / k)
    return c if value == obs.value else (1.0 - c) / (k - 1)


def uniform(n: int) -> list[float]:
    return [1.0 / n] * n


def normalize(weights: Sequence[float]) -> list[float]:
    total = sum(weights)
    if total <= 0:
        raise ValueError("belief가 모두 0이다")
    return [w / total for w in weights]


def compute_belief(
    items: Sequence[Item],
    attributes: Mapping[str, AttributeDef],
    observations: Iterable[Observation],
    prior: Sequence[float] | None = None,
) -> list[float]:
    """prior × Π 가능도를 정규화한 사후분포. 정의에 없는 속성의 관측은 무시한다.

    @docid core.compute-belief
    @see docs/ig-engine.md#1-답변-가능도-모델
    """
    weights = list(prior) if prior is not None else uniform(len(items))
    for obs in observations:
        attr = attributes.get(obs.attribute)
        if attr is None:
            continue
        weights = [w * likelihood(item, obs, attr) for w, item in zip(weights, items)]
    return normalize(weights)


def entropy(probs: Iterable[float]) -> float:
    """섀넌 엔트로피 (bits)."""
    return -sum(p * math.log2(p) for p in probs if p > 0)
