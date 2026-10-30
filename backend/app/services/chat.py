"""대화 관리자: 사용자 입력을 IG 엔진의 상태로 반영하고, 엔진의 결정을 응답으로 바꾼다.

입력 해석 순서: 버튼 답(answer) → LLM 파서 → (파서가 없거나 실패하면) 보기 문자열 매칭.
추천을 보여준 뒤에도 대화가 이어져, 조건 추가·변경·상품 제외로 추천 목록을 조정한다.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.catalog.repository import Product
from app.catalog.spec import AttributeSpec, CategorySpec
from app.core.adapters import from_catalog
from app.core.engine import Ask, Decision, EngineConfig, IGEngine, Limitation, Recommend
from app.core.model import DONT_CARE, PreferenceState
from app.core.recommender import RankedItem
from app.schemas import DebugInfo, Option, Question, QuestionScore, Recommendation
from app.services.parser import ParseResult

DONT_CARE_LABEL = "상관없음"
DONT_CARE_WORDS = ("상관없", "상관 없", "아무거나", "괜찮아", "무관")
DEBUG_TOP_N = 5

# 추정 선호의 확신도 처리 (docs/roadmap.md Phase 3+)
INFER_AS_EXPLICIT = 0.85  # 이상이면 직접 말한 조건으로 본다
INFER_MIN = 0.5  # 미만이면 버린다

# (spec, 발화, 대기 중인 질문, 지금까지 명시 조건, 현재 추천 요약) -> 해석 결과 (실패 시 None)
Parser = Callable[[CategorySpec, str, str | None, dict[str, str], list[str] | None],
                  ParseResult | None]


class InvalidAnswer(ValueError):
    """정의에 없는 속성이나 값으로 답한 경우."""


@dataclass
class Catalog:
    """한 카테고리의 정의, 상품, 엔진."""

    spec: CategorySpec
    products: dict[str, Product]
    engine: IGEngine


def build_catalog(
    spec: CategorySpec, products: Sequence[Product], config: EngineConfig = EngineConfig()
) -> Catalog:
    attributes, items = from_catalog(spec, products)
    return Catalog(spec, {p.id: p for p in products}, IGEngine(items, attributes, config))


@dataclass
class ChatState:
    """sessions.state에 저장되는 대화 상태.

    @see docs/architecture.md#세션-상태
    """

    category_id: str
    turn: int = 0
    preference: PreferenceState = field(default_factory=PreferenceState)
    pending: str | None = None  # 지금 답을 기다리는 질문의 속성
    # True면 추천을 한 번 이상 보여준 상태. 이후 입력은 추천 목록 조정으로 처리한다.
    finished: bool = False
    last_recommended: list[str] = field(default_factory=list)
    # "더 싼/비싼 걸로": 조건이 똑같이 맞는 상품 사이의 가격 정렬 (cheaper / pricier / None)
    price_order: str | None = None

    @classmethod
    def from_dict(cls, data: dict, default_category: str) -> "ChatState":
        return cls(
            category_id=data.get("category_id", default_category),
            turn=data.get("turn", 0),
            preference=PreferenceState.from_dict(data.get("preference", {})),
            pending=data.get("pending"),
            finished=data.get("finished", False),
            last_recommended=list(data.get("last_recommended", [])),
            price_order=data.get("price_order"),
        )

    def to_dict(self) -> dict:
        return {
            "category_id": self.category_id,
            "turn": self.turn,
            "preference": self.preference.to_dict(),
            "pending": self.pending,
            "finished": self.finished,
            "last_recommended": list(self.last_recommended),
            "price_order": self.price_order,
        }


@dataclass
class TurnResult:
    reply: str
    state: ChatState
    debug: DebugInfo
    question: Question | None = None
    recommendations: list[Recommendation] = field(default_factory=list)


# ---------------------------------------------------------------- 질문·답 해석

def value_label(attr: AttributeSpec, value: str) -> str:
    if value == DONT_CARE:
        return DONT_CARE_LABEL
    return next((v.label for v in attr.values if v.value == value), value)


def build_question(spec: CategorySpec, attribute: str) -> Question:
    attr = spec.attribute(attribute)
    options = [Option(value=v.value, label=v.label) for v in attr.values]
    options.append(Option(value=DONT_CARE, label=DONT_CARE_LABEL))
    return Question(attribute=attr.key, label=attr.label, text=attr.question or attr.label,
                    options=options)


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.casefold()) if len(t) >= 2]


def match_text(attr: AttributeSpec, text: str) -> str | None:
    """보기 이름·값과 문자열로 맞춰 본다. 하나로 정해지지 않으면 None.

    "불필요"처럼 다른 보기("필요")를 포함하는 표현은 더 긴 쪽만 인정한다.

    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/llm-parser.md#1-입력-해석-순서
    """
    normalized = text.casefold()
    if any(w in normalized for w in DONT_CARE_WORDS):
        return DONT_CARE
    hits: dict[str, list[str]] = {}
    for v in attr.values:
        for token in _tokens(v.label) + _tokens(v.value):
            if token in normalized:
                hits.setdefault(v.value, []).append(token)
    longest = {value: max(tokens, key=len) for value, tokens in hits.items()}
    kept = [
        value for value, token in longest.items()
        if not any(token != other and token in other for other in longest.values())
    ]
    return kept[0] if len(kept) == 1 else None


def validate_answer(spec: CategorySpec, attribute: str, value: str) -> None:
    try:
        attr = spec.attribute(attribute)
    except KeyError as e:
        raise InvalidAnswer(f"알 수 없는 속성: {attribute}") from e
    if value != DONT_CARE and value not in {v.value for v in attr.values}:
        raise InvalidAnswer(f"{attribute}에 없는 값: {value}")


def apply_parsed(state: ChatState, parsed: ParseResult, spec: CategorySpec) -> list[str]:
    """해석 결과를 상태에 반영하고, 반영한 내용을 사람이 읽을 문구로 돌려준다.

    @docid chat.apply-parsed
    @see docs/llm-parser.md#4-해석-결과를-대화-상태에-반영
    """
    pref, applied = state.preference, []
    for c in parsed.constraints:
        attr = spec.attribute(c.attribute)
        label = f"{attr.label} {value_label(attr, c.value)}"
        if c.kind == "explicit" or c.confidence >= INFER_AS_EXPLICIT:
            unchanged = (pref.explicit.get(c.attribute) == c.value
                         or (c.value == DONT_CARE and c.attribute in pref.dont_care))
            pref.answer(c.attribute, c.value)
            if not unchanged:  # 이미 있는 조건을 다시 말한 것은 알리지 않는다
                applied.append(label)
        elif c.confidence >= INFER_MIN and c.value != DONT_CARE:
            if c.attribute in pref.explicit or c.attribute in pref.dont_care:
                continue  # 직접 말한 조건이 우선
            previous = pref.inferred.get(c.attribute)
            pref.infer(c.attribute, c.value, c.confidence, c.reason)
            if previous is None or previous.value != c.value:
                applied.append(f"{label}(추정)")
    for rank in parsed.exclude_ranks:
        if rank <= len(state.last_recommended):
            pref.exclude(state.last_recommended[rank - 1])
            applied.append(f"{rank}번 상품 제외")
    if parsed.price_order in ("cheaper", "pricier"):
        state.price_order = parsed.price_order
        applied.append("더 저렴한 순" if parsed.price_order == "cheaper" else "더 비싼 순")
    return applied


def price_tie_key(catalog: Catalog, order: str | None):
    """확률이 같은 상품 사이를 가격순으로. 가격이 없으면 뒤로."""
    if order not in ("cheaper", "pricier"):
        return None
    sign = 1 if order == "cheaper" else -1

    def key(item) -> float:
        price = catalog.products[item.id].price
        return float("inf") if price is None else sign * price

    return key


def recommendation_context(catalog: Catalog, state: ChatState) -> list[str] | None:
    """파서에 넘길 현재 추천 요약 ("더 싼 걸로" 같은 상대 표현 해석용)."""
    if not state.finished or not state.last_recommended:
        return None
    spec, lines = catalog.spec, []
    for pid in state.last_recommended:
        product = catalog.products.get(pid)
        if product is None:
            continue
        attrs = ", ".join(
            f"{spec.attribute(k).label}={value_label(spec.attribute(k), v)}"
            for k, v in product.attributes.items() if k != "brand"
        )
        price = f"${product.price:.2f}" if product.price is not None else "가격 정보 없음"
        lines.append(f"{product.title[:60]} | {price} | {attrs}")
    return lines


# ---------------------------------------------------------------- 응답 생성

def reasons_for(spec: CategorySpec, ranked: RankedItem, state: PreferenceState) -> list[str]:
    reasons = []
    for key in ranked.matched:
        attr = spec.attribute(key)
        reasons.append(f"{attr.label}: {value_label(attr, state.explicit[key])}")
    for key in ranked.unknown:
        reasons.append(f"{spec.attribute(key).label}: 상품 정보에 없어 확인 못 함")
    for key in ranked.violated:
        attr = spec.attribute(key)
        actual = ranked.item.attributes.get(key, "")
        reasons.append(f"{attr.label}: {value_label(attr, actual)} (요청하신 조건과 다름)")
    for key, inf in state.inferred.items():
        attr = spec.attribute(key)
        if ranked.item.attributes.get(key) == inf.value:
            reasons.append(f"{attr.label}: {value_label(attr, inf.value)} (말씀하신 상황을 고려)")
    return reasons


def to_recommendation(catalog: Catalog, ranked: RankedItem, state: PreferenceState) -> Recommendation:
    product = catalog.products[ranked.item.id]
    return Recommendation(
        product_id=product.id, title=product.title, probability=ranked.probability,
        price=product.price, image_url=product.image_url,
        reasons=reasons_for(catalog.spec, ranked, state),
    )


TIE_ORDER_TEXT = {"cheaper": "가격이 낮은 순", "pricier": "가격이 높은 순"}


def describe_limitation(spec: CategorySpec, lim: Limitation, price_order: str | None = None) -> str:
    if lim.code == "tied_candidates":
        order = TIE_ORDER_TEXT.get(price_order or "", "평점이 좋은 순")
        return (f"알려주신 조건으로는 {lim.detail['count']}개 상품이 똑같이 잘 맞아서, "
                f"{order}으로 골랐어요.")
    if lim.code == "unknown_attributes":
        labels = ", ".join(spec.attribute(k).label for k in lim.detail)
        return f"{labels}은(는) 일부 상품 정보에 없어 확인하지 못했어요."
    if lim.code == "constraint_relaxed":
        return "조건을 모두 만족하는 상품이 부족해서, 일부 조건이 다른 상품도 함께 보여드려요."
    return ""


STOP_MESSAGES = {
    "budget": "말씀해 주신 조건으로 상품을 골라봤어요.",
    "confident": "조건에 잘 맞는 상품을 찾았어요.",
    "no_informative_question": "더 여쭤봐도 후보가 크게 줄지 않아서, 지금 조건으로 골라봤어요.",
    "refined": "조건을 반영해서 다시 골라봤어요.",
}
REFINE_HINT = "더 고려할 점이나 바꾸고 싶은 조건이 있으면 말씀해 주세요. (예: 더 저렴한 걸로, 1번 말고)"


def non_shopping_reply(spec: CategorySpec, request_type: str) -> str:
    if request_type == "unsafe":
        return f"죄송하지만 그 요청은 도와드릴 수 없어요. {spec.name}을(를) 찾는 건 계속 도와드릴게요."
    if request_type == "other_product":
        return f"지금은 {spec.name}만 추천할 수 있어요."
    return f"저는 {spec.name} 추천을 도와드리고 있어요."


def build_debug(catalog: Catalog, decision: Decision, state: ChatState,
                request_type: str | None = None) -> DebugInfo:
    spec, action = catalog.spec, decision.action
    debug = DebugInfo(
        turn=state.turn,
        entropy=decision.entropy,
        top_candidates=[to_recommendation(catalog, r, state.preference)
                        for r in decision.top[:DEBUG_TOP_N]],
        preferences=state.preference.to_dict(),
        request_type=request_type,
    )
    if isinstance(action, Ask):
        debug.selected_attribute = action.attribute
        debug.expected_ig = action.expected_ig
        debug.question_scores = [
            QuestionScore(attribute=c.attribute, label=spec.attribute(c.attribute).label,
                          expected_ig=c.expected_ig)
            for c in action.candidates
        ]
    else:
        debug.stop_reason = action.reason
    return debug


def _peek(catalog: Catalog, state: ChatState) -> Decision:
    """상태를 바꾸지 않는 현재 결정 (응답 없이 디버그만 필요할 때)."""
    if state.finished:
        return catalog.engine.recommend_now(state.preference,
                                            tie_key=price_tie_key(catalog, state.price_order))
    return catalog.engine.step(state.preference)


def _same_question(catalog: Catalog, state: ChatState, prefix: str,
                   request_type: str | None = None) -> TurnResult:
    """대기 중인 질문을 다시 보여준다 (질문이 없으면 prefix만)."""
    debug = build_debug(catalog, _peek(catalog, state), state, request_type)
    if state.pending is None:
        hint = f"\n{REFINE_HINT}" if state.finished else ""
        return TurnResult(prefix + hint, state, debug)
    question = build_question(catalog.spec, state.pending)
    return TurnResult(f"{prefix}\n\n{question.text}", state, debug, question)


# ---------------------------------------------------------------- 한 턴

def handle_message(
    state: ChatState, message: str, catalog: Catalog,
    answer: tuple[str, str] | None = None,
    parser: Parser | None = None,
) -> TurnResult:
    """대화 한 턴을 처리한다.

    1. 입력 해석: 버튼 답(answer) → LLM 파서 → 보기 문자열 매칭
    2. 결정: 질문 단계면 엔진이 질문/추천, 추천을 보여준 뒤면 바로 재추천

    @docid chat.handle-turn
    @see backend/README.md#구조
    @see docs/architecture.md#1-한눈에-보기
    @see docs/doc-links.md#3-docid
    @see docs/llm-parser.md#llm-파서와-멀티턴-대화
    """
    spec, pref = catalog.spec, state.preference
    state.turn += 1
    first_turn = (not state.finished and state.pending is None
                  and not pref.asked and not pref.explicit and not pref.inferred)
    applied: list[str] = []  # 이번 턴에 바뀐 것 (사용자에게 알림)
    understood = False  # 쇼핑 관련 내용을 하나라도 알아들었는가 (바뀐 것이 없어도)
    request_type = None

    if answer is not None:
        validate_answer(spec, *answer)
        pref.answer(*answer)
        if answer[0] == state.pending:
            state.pending = None
    else:
        parsed = None
        if parser is not None:
            parsed = parser(spec, message, state.pending, dict(pref.explicit),
                            recommendation_context(catalog, state))
        if parsed is not None:
            request_type = parsed.request_type
            if parsed.request_type != "shopping":
                return _same_question(catalog, state, non_shopping_reply(spec, parsed.request_type),
                                      request_type)
            applied = apply_parsed(state, parsed, spec)
            understood = bool(applied or parsed.constraints)

        pending = state.pending
        if pending is not None and (pending in pref.explicit or pending in pref.dont_care):
            state.pending = None
        elif pending is not None and not understood:
            # 파서가 없거나 아무것도 못 찾았으면 보기 이름과 맞춰 본다
            value = match_text(spec.attribute(pending), message)
            if value is None:
                return _same_question(catalog, state, "잘 이해하지 못했어요. 아래 보기에서 골라 주세요.",
                                      request_type)
            pref.answer(pending, value)
            applied.append(f"{spec.attribute(pending).label} {value_label(spec.attribute(pending), value)}")
            understood = True
            state.pending = None
        elif pending is not None:
            # 질문에는 답하지 않고 다른 정보만 줬다 → 그 질문은 안 물은 것으로 되돌린다
            if pending in pref.asked:
                pref.asked.remove(pending)
            state.pending = None
        elif state.finished and not understood:
            if parser is None:
                return _same_question(catalog, state,
                                      "추가 조건을 이해하려면 LLM 연결이 필요해요. 새 대화로 다시 찾을 수 있어요.")
            return _same_question(catalog, state, "어떤 점을 바꾸고 싶으신지 조금만 더 알려주세요.",
                                  request_type)

    ack = f"반영했어요: {', '.join(applied)}.\n" if applied else ""

    if state.finished:
        decision = catalog.engine.recommend_now(pref, tie_key=price_tie_key(catalog, state.price_order))
    else:
        decision = catalog.engine.step(pref)
    action = decision.action
    debug = build_debug(catalog, decision, state, request_type)

    if isinstance(action, Ask):
        pref.mark_asked(action.attribute)
        state.pending = action.attribute
        question = build_question(spec, action.attribute)
        intro = f"{spec.name} 추천을 도와드릴게요. 몇 가지만 여쭤볼게요.\n\n" if first_turn else ""
        return TurnResult(intro + ack + question.text, state, debug, question)

    assert isinstance(action, Recommend)
    state.finished = True
    state.pending = None
    state.last_recommended = [r.item.id for r in action.items]
    lines = [ack + STOP_MESSAGES.get(action.reason, "추천 상품이에요.")]
    lines += [text for lim in action.limitations
              if (text := describe_limitation(spec, lim, state.price_order))]
    lines.append(REFINE_HINT)
    return TurnResult(
        "\n".join(lines), state, debug,
        recommendations=[to_recommendation(catalog, r, pref) for r in action.items],
    )
