"""현재 belief로 Top-k 상품을 고르고, 조건 충족 여부를 붙인다.

@see docs/ig-engine.md#ig-엔진
"""

from collections.abc import Callable, Sequence
from typing import Any
from dataclasses import dataclass, field

from app.core.model import Item, PreferenceState


@dataclass
class RankedItem:
    item: Item
    probability: float
    # 사용자가 직접 말한 조건 중 이 상품이 맞는 것 / 데이터에 없어 확인 못 한 것 / 어긋나는 것
    matched: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    violated: list[str] = field(default_factory=list)


def check_constraints(item: Item, state: PreferenceState) -> tuple[list[str], list[str], list[str]]:
    matched, unknown, violated = [], [], []
    for attribute, wanted in state.explicit.items():
        actual = item.attributes.get(attribute)
        if actual is None:
            unknown.append(attribute)
        elif actual == wanted:
            matched.append(attribute)
        else:
            violated.append(attribute)
    return matched, unknown, violated


# 같은 확률로 보는 자릿수 (부동소수점 오차로 동률이 갈리지 않게)
PROB_DIGITS = 12

# 확률이 같은 상품 사이의 순서. 작을수록 앞(숫자나 튜플). 기본은 score 내림차순.
TieKey = Callable[[Item], Any]


def rank_items(items: Sequence[Item], belief: Sequence[float],
               tie_key: TieKey | None = None) -> list[int]:
    """확률 내림차순 인덱스. 동률이면 tie_key(기본: score가 큰 것), 그다음 id 순."""
    key = tie_key or (lambda item: -item.score)
    return sorted(range(len(items)),
                  key=lambda i: (-round(belief[i], PROB_DIGITS), key(items[i]), items[i].id))


def recommend(
    items: Sequence[Item], belief: Sequence[float], state: PreferenceState, k: int = 3,
    tie_key: TieKey | None = None,
) -> list[RankedItem]:
    """Top-k. 사용자가 말한 조건을 어기는 상품은 빼고, 모자랄 때만 채운다(violated 표시).

    @docid core.recommend
    @see docs/ig-engine.md#4-추천과-한계-표시
    """
    ranked = []
    for i in rank_items(items, belief, tie_key):
        matched, unknown, violated = check_constraints(items[i], state)
        ranked.append(RankedItem(items[i], belief[i], matched, unknown, violated))
    satisfying = [r for r in ranked if not r.violated]
    return (satisfying + [r for r in ranked if r.violated])[:k]
