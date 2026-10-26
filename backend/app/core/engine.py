"""IG 엔진: 한 턴마다 '질문할지, 추천할지'를 결정한다.

상태(PreferenceState)를 받아 결정(Ask | Recommend)을 돌려줄 뿐 상태를 바꾸지 않는다.
답변 반영(state.answer)과 질문 기록(state.mark_asked)은 호출하는 쪽(대화 관리자, 시뮬레이터)이 한다.

@see docs/ig-engine.md
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.core.belief_tracker import compute_belief, entropy
from app.core.model import AttributeDef, Item, PreferenceState
from app.core.question_selector import (
    InformationGainPolicy, QuestionPolicy, QuestionScore, candidate_questions,
)
from app.core.recommender import RankedItem, TieKey, recommend

# 같은 확률로 보는 오차
TIE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class EngineConfig:
    noise: float = 0.05  # 사용자 답이 상품 데이터와 어긋날 확률 ε
    max_questions: int = 4
    min_information_gain: float = 0.05  # bits. 이보다 작으면 더 물어도 소용없다고 본다
    confident_mass: float = 0.9  # Top-k 확률 합이 이 이상이면 바로 추천
    top_k: int = 3


@dataclass
class Limitation:
    """추천 근거의 한계. 대화 관리자가 문장으로 바꿔 사용자에게 알린다.

    code:
      tied_candidates      detail: {"count": 1위와 같은 확률인 상품 수}
      unknown_attributes   detail: {속성: 추천 상품 중 그 속성이 unknown인 수}
      constraint_relaxed   detail: {"count": 조건을 어기는데 포함된 추천 수}
    """

    code: str
    detail: dict


@dataclass
class Ask:
    attribute: str
    expected_ig: float
    candidates: list[QuestionScore]


@dataclass
class Recommend:
    items: list[RankedItem]
    # budget: 질문 예산 소진 / confident: 확률이 충분히 모임 / no_informative_question: 더 물어도 구별 불가
    # refined: 추천 후 사용자가 조건을 바꿔 다시 추천
    reason: str
    limitations: list[Limitation] = field(default_factory=list)


@dataclass
class Decision:
    action: Ask | Recommend
    belief: list[float]
    entropy: float
    # Developer Panel용 상위 후보 (확률순)
    top: list[RankedItem]


class IGEngine:
    """@docid core.engine"""

    def __init__(
        self,
        items: Sequence[Item],
        attributes: Mapping[str, AttributeDef],
        config: EngineConfig = EngineConfig(),
        policy: QuestionPolicy | None = None,
        prior: Sequence[float] | None = None,
    ):
        if not items:
            raise ValueError("상품이 없다")
        self.items = list(items)
        self.attributes = dict(attributes)
        self.config = config
        self.policy = policy or InformationGainPolicy()
        self.prior = list(prior) if prior is not None else None

    def belief(self, state: PreferenceState) -> list[float]:
        prior = self.prior or [1.0] * len(self.items)
        if state.excluded:
            excluded = set(state.excluded)
            prior = [0.0 if item.id in excluded else p for item, p in zip(self.items, prior)]
            if not any(prior):  # 전부 제외되면 제외를 무시한다
                prior = self.prior or [1.0] * len(self.items)
        return compute_belief(self.items, self.attributes,
                              state.observations(self.config.noise), prior)

    def recommend_now(self, state: PreferenceState, reason: str = "refined",
                      tie_key: TieKey | None = None) -> Decision:
        """질문 없이 지금 상태로 바로 추천한다 (추천 후 조건을 바꾼 경우).

        tie_key: 확률이 같은 상품 사이의 순서 (예: "더 싼 걸로" → 가격 오름차순)

        @docid core.engine-recommend-now
        """
        belief = self.belief(state)
        top = self._top(belief, state, tie_key)
        items = top[: self.config.top_k]
        return Decision(Recommend(items, reason, self._limitations(belief, items)),
                        belief, entropy(belief), top)

    def _top(self, belief: list[float], state: PreferenceState,
             tie_key: TieKey | None = None) -> list[RankedItem]:
        ranked = recommend(self.items, belief, state, k=len(self.items), tie_key=tie_key)
        excluded = set(state.excluded)
        return [r for r in ranked if r.item.id not in excluded][: max(self.config.top_k, 5)]

    def step(self, state: PreferenceState) -> Decision:
        """@docid core.engine-step"""
        cfg = self.config
        belief = self.belief(state)
        top = self._top(belief, state)
        candidates = candidate_questions(self.items, belief, self.attributes, state, cfg.noise)
        informative = [c for c in candidates if c.expected_ig >= cfg.min_information_gain]
        top_mass = sum(r.probability for r in top[: cfg.top_k])

        reason = None
        if len(state.asked) >= cfg.max_questions:
            reason = "budget"
        elif top_mass >= cfg.confident_mass:
            reason = "confident"
        elif not informative:
            reason = "no_informative_question"

        if reason is None:
            choice = self.policy.choose(candidates)
            if choice is not None:
                return Decision(Ask(choice.attribute, choice.expected_ig, candidates),
                                belief, entropy(belief), top)
            reason = "no_informative_question"

        items = top[: cfg.top_k]
        return Decision(Recommend(items, reason, self._limitations(belief, items)),
                        belief, entropy(belief), top)

    def _limitations(self, belief: list[float], recs: list[RankedItem]) -> list[Limitation]:
        out = []
        best = max(belief)
        tied = sum(1 for p in belief if abs(p - best) <= TIE_TOLERANCE * max(best, 1.0))
        if tied > len(recs):
            out.append(Limitation("tied_candidates", {"count": tied}))
        unknown: dict[str, int] = {}
        for r in recs:
            for attribute in r.unknown:
                unknown[attribute] = unknown.get(attribute, 0) + 1
        if unknown:
            out.append(Limitation("unknown_attributes", unknown))
        relaxed = sum(1 for r in recs if r.violated)
        if relaxed:
            out.append(Limitation("constraint_relaxed", {"count": relaxed}))
        return out
