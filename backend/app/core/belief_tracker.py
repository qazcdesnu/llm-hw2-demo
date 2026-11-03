"""목표 상품에 대한 확률분포(belief)를 관측으로부터 계산한다.

@see docs/ig-engine.md#ig-엔진
"""

import math
from collections.abc import Iterable, Mapping, Sequence

from app.core.model import AttributeDef, Item, Observation, RangeObservation, SetObservation


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


def range_likelihood(item: Item, obs: RangeObservation, attr: AttributeDef) -> float:
    """P(범위 관측 | 목표 상품 = item). 구간 속성의 실제 숫자(가격 등)로 판정한다.

    구간 답과 같은 꼴: 범위 안이면 c, 밖이면 (1-c)/(k-1), 숫자가 없으면 1/k (k는 그 속성의 구간 수).
    구간 경계에 걸친 예산("$15~25")을 구간 하나로 뭉개지 않으려고 쓴다.

    @docid core.range-likelihood
    @see docs/ig-engine.md#1-답변-가능도-모델
    """
    k = len(attr.values)
    x = item.numbers.get(obs.attribute)
    if x is None:
        return 1.0 / k
    c = max(obs.confidence, 1.0 / k)
    return c if obs.contains(x) else (1.0 - c) / (k - 1)


def set_likelihood(item: Item, obs: SetObservation, attr: AttributeDef) -> float:
    """P('values 중 하나' | 목표 상품 = item). 집합 안이면 c, 밖이면 (1-c)/(k-1), unknown이면 1/k.

    한 값 답과 같은 꼴이라, 집합이 한 값이면 likelihood와 같다.

    @docid core.set-likelihood
    @see docs/ig-engine.md#1-답변-가능도-모델
    """
    k = len(attr.values)
    value = item.attributes.get(obs.attribute)
    if value is None or value not in attr.values:
        return 1.0 / k
    c = max(obs.confidence, 1.0 / k)
    return c if value in obs.values else (1.0 - c) / (k - 1)


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
    observations: Iterable[Observation | RangeObservation | SetObservation],
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
        lik = (range_likelihood if isinstance(obs, RangeObservation)
               else set_likelihood if isinstance(obs, SetObservation) else likelihood)
        weights = [w * lik(item, obs, attr) for w, item in zip(weights, items)]
    return normalize(weights)


def entropy(probs: Iterable[float]) -> float:
    """섀넌 엔트로피 (bits)."""
    return -sum(p * math.log2(p) for p in probs if p > 0)
