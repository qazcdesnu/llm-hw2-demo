"""다음에 물을 속성을 고른다.

모든 정책은 같은 후보 목록(기대 정보량 포함)에서 고르고, 고르는 방법만 다르다.
평가에서 Random / Fixed / IG를 공정하게 비교하기 위함이다.

@see docs/ig-engine.md#ig-엔진
"""

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.belief_tracker import likelihood
from app.core.model import AttributeDef, Item, Observation, PreferenceState


@dataclass(frozen=True)
class QuestionScore:
    attribute: str
    expected_ig: float  # bits


def expected_information_gain(
    items: Sequence[Item], belief: Sequence[float], attr: AttributeDef, noise: float
) -> float:
    """이 속성을 물었을 때 목표 상품에 대해 얻을 기대 정보량 I(T; A) (bits).

    I = Σ_p Σ_v P(p) L(v|p) log2(L(v|p) / P(v)),  P(v) = Σ_p P(p) L(v|p)
    답변은 belief_tracker.likelihood와 같은 모델(확신도 1-noise)로 생성된다고 본다.

    @docid core.expected-ig
    @see docs/ig-engine.md#3-질문-선택
    """
    rows = [
        [likelihood(item, Observation(attr.key, v, 1.0 - noise), attr) for v in attr.values]
        for item in items
    ]
    p_answer = [sum(b * row[j] for b, row in zip(belief, rows)) for j in range(len(attr.values))]
    info = 0.0
    for b, row in zip(belief, rows):
        if b <= 0:
            continue
        for j, lik in enumerate(row):
            if lik > 0 and p_answer[j] > 0:
                info += b * lik * math.log2(lik / p_answer[j])
    return max(info, 0.0)


def candidate_questions(
    items: Sequence[Item],
    belief: Sequence[float],
    attributes: Mapping[str, AttributeDef],
    state: PreferenceState,
    noise: float,
) -> list[QuestionScore]:
    """물을 수 있는 속성(askable, 아직 답·질문하지 않음)과 기대 정보량. 정보량 내림차순.

    @see docs/ig-engine.md#3-질문-선택
    """
    resolved = state.resolved()
    scores = [
        QuestionScore(a.key, expected_information_gain(items, belief, a, noise))
        for a in attributes.values()
        if a.askable and a.key not in resolved
    ]
    return sorted(scores, key=lambda s: (-s.expected_ig, s.attribute))


class QuestionPolicy(Protocol):
    name: str

    def choose(self, candidates: Sequence[QuestionScore]) -> QuestionScore | None: ...


class InformationGainPolicy:
    """기대 정보량이 가장 큰 질문.

    @see docs/ig-engine.md#3-질문-선택
    """

    name = "ig"

    def choose(self, candidates: Sequence[QuestionScore]) -> QuestionScore | None:
        return max(candidates, key=lambda s: s.expected_ig, default=None)


class RandomPolicy:
    """무작위 질문 (baseline).

    @see docs/ig-engine.md#3-질문-선택
    """

    name = "random"

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)

    def choose(self, candidates: Sequence[QuestionScore]) -> QuestionScore | None:
        ordered = sorted(candidates, key=lambda s: s.attribute)  # 입력 순서와 무관하게 재현
        return self._rng.choice(ordered) if ordered else None


class FixedOrderPolicy:
    """정해진 순서대로 질문 (baseline).

    @see docs/ig-engine.md#3-질문-선택
    """

    name = "fixed"

    def __init__(self, order: Sequence[str]):
        self._rank = {key: i for i, key in enumerate(order)}

    def choose(self, candidates: Sequence[QuestionScore]) -> QuestionScore | None:
        ordered = [c for c in candidates if c.attribute in self._rank]
        return min(ordered, key=lambda s: self._rank[s.attribute], default=None)
