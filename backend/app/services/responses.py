"""응답 표현: 엔진의 결정을 사용자에게 보여줄 말과 데이터로 바꾼다.

질문 문장은 질문 작성기(questioner.py, LLM)가 만들고, 실패하면 카테고리 정의의 고정 질문을 쓴다.
추천 안내·한계·거절 문구와 추천 근거는 이 파일의 템플릿이다. 말투는 친한 친구의 반말로 맞춘다.
문구는 한국어·영어 두 벌이고, 사용자 발화의 언어(lang)를 따른다.

@see docs/architecture.md#1-한눈에-보기
@see docs/architecture.md#3-대화-한-턴의-흐름
@see docs/briefing.md#4-아키텍처와-코드-지도
@see docs/llm-parser.md#8-요청-분류와-safety
"""

from app.catalog.repository import Product
from app.catalog.spec import AttributeSpec, CategorySpec
from app.core.engine import Limitation
from app.core.model import DONT_CARE, PreferenceState
from app.core.recommender import RankedItem
from app.schemas import Option, Question, Recommendation
from app.services.memory import ANY_OF, RANGE

TEXT = {
    "ko": {
        "dont_care": "상관없음",
        "stop.budget": "말해 준 조건으로 골라 봤어.",
        "stop.confident": "조건에 딱 맞는 걸 찾았어!",
        "stop.no_informative_question": "더 물어봐도 후보가 크게 안 줄어서, 지금 조건으로 골라 봤어.",
        "stop.refined": "말한 거 반영해서 다시 골라 봤어.",
        "stop.default": "추천 상품이야.",
        "refine_hint": "더 고려할 점이나 바꾸고 싶은 게 있으면 말해 줘. (예: 더 저렴한 걸로, 1번 말고)",
        "not_understood": "음, 잘 못 알아들었어. 아래 보기에서 골라 줄래?",
        "unclear_prefix": "음, 그건 잘 모르겠어.",
        "refine_unclear": "어떤 걸 바꾸고 싶은지 조금만 더 말해 줄래?",
        "refine_needs_llm": "추가 조건을 알아들으려면 LLM 연결이 필요해. 새 대화로 다시 찾을 수 있어.",
        "intro": "{name} 고르는 거 도와줄게! 몇 가지만 물어볼게.",
        "unsafe": "미안, 그건 추천해 줄 수 없어.",
        "other_product": "지금은 {name}만 추천할 수 있어.",
        "off_topic": "나는 {name} 고르는 걸 도와주고 있어.",
        "ack": "좋아, {items}까지 챙길게.",
        "guess": "{items} 쪽이 맞을 것 같아서 그렇게 볼게. 아니면 말해 줘.",
        "excluded": "{rank}번 상품 제외",
        "order.cheaper": "더 저렴한 순",
        "order.pricier": "더 비싼 순",
        "tie_order.cheaper": "가격이 낮은 순",
        "tie_order.pricier": "가격이 높은 순",
        "tie_order.rating": "평점이 좋은 순",
        "tie_order.relevance": "얘기해 준 내용에 가까운 순",
        "tied": "말해 준 조건으로는 {count}개가 똑같이 잘 맞아서, {order}으로 골랐어.",
        "unknown_attributes": "{labels}은(는) 일부 상품 정보에 없어서 확인 못 했어.",
        "relaxed": "조건을 모두 만족하는 상품이 부족해서, 일부 조건이 다른 것도 같이 보여 줄게.",
        "reason.unknown": "{label}: 상품 정보에 없어 확인 못 함",
        "reason.violated": "{label}: {value} (말한 조건과 다름)",
        "confirm": "정리해 볼게: {items}. 이대로 찾아볼까, 아니면 바뀐 게 있어?",
        "confirm_last": "마지막으로 정리할게: {items}. 이대로 찾아볼까?",
        "confirm_label": "확인",
        "confirm_yes": "이대로 추천해 줘",
        "stop.confirmed": "좋아, 이 조건으로 골라 봤어.",
        "or": " 또는 ",
        "range.both": "${low:g}~{high:g}",
        "range.max": "${high:g} 이하",
        "range.min": "${low:g} 이상",
    },
    "en": {
        "dont_care": "Doesn't matter",
        "stop.budget": "Here's what I picked from what you told me.",
        "stop.confident": "Found ones that fit exactly!",
        "stop.no_informative_question": "More questions wouldn't narrow it down much, so here's what fits so far.",
        "stop.refined": "Updated the picks with what you said.",
        "stop.default": "Here are my picks.",
        "refine_hint": "Anything else to consider or change? (e.g. something cheaper, not #1)",
        "not_understood": "Hmm, I didn't quite get that. Could you pick one of the options below?",
        "unclear_prefix": "Hmm, not sure about that one.",
        "refine_unclear": "Could you tell me a bit more about what you'd like to change?",
        "refine_needs_llm": "I need the LLM connection to understand new conditions. You can start a new chat.",
        "intro": "Happy to help you pick {name}! Just a few questions.",
        "unsafe": "Sorry, I can't recommend that.",
        "other_product": "Right now I can only recommend {name}.",
        "off_topic": "I'm here to help you pick {name}.",
        "ack": "Got it, I'll keep {items} in mind.",
        "guess": "Sounds like you'd want {items}, so I'll go with that. Tell me if not.",
        "excluded": "excluding #{rank}",
        "order.cheaper": "cheaper first",
        "order.pricier": "pricier first",
        "tie_order.cheaper": "lowest price",
        "tie_order.pricier": "highest price",
        "tie_order.rating": "rating",
        "tie_order.relevance": "how close they are to what you described",
        "tied": "{count} products fit what you said equally well, so I ordered them by {order}.",
        "unknown_attributes": "Some product listings don't mention {labels}, so I couldn't check that.",
        "relaxed": "Not enough products meet every condition, so I'm also showing some that differ a bit.",
        "reason.unknown": "{label}: not listed, couldn't check",
        "reason.violated": "{label}: {value} (differs from what you asked)",
        "confirm": "Quick recap: {items}. Should I go with this, or has anything changed?",
        "confirm_last": "Last check: {items}. Shall I go with this?",
        "confirm_label": "Confirm",
        "confirm_yes": "Looks good, show me",
        "stop.confirmed": "Great, here's what I picked with those conditions.",
        "or": " or ",
        "range.both": "${low:g}–{high:g}",
        "range.max": "up to ${high:g}",
        "range.min": "${low:g}+",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """문구 하나. 없는 언어는 한국어로."""
    return TEXT.get(lang, TEXT["ko"])[key].format(**kwargs)


def value_label(attr: AttributeSpec, value: str, lang: str = "ko") -> str:
    if value == DONT_CARE:
        return t(lang, "dont_care")
    return next((v.label_for(lang) for v in attr.values if v.value == value), value)


def range_label(low: float | None, high: float | None, lang: str = "ko") -> str:
    """숫자 범위 ('$10~25', '$30 이하', '$60 이상')."""
    if low is not None and high is not None:
        return t(lang, "range.both", low=low, high=high)
    if high is not None:
        return t(lang, "range.max", high=high)
    return t(lang, "range.min", low=low)


def change_label(spec: CategorySpec, attribute: str, value: str, lang: str = "ko",
                 low: float | None = None, high: float | None = None, values: list[str] | None = None) -> str:
    """새로 알게 된 것을 사용자에게 알릴 때의 문구.

    예: '마이크 필요', 'Mic: Needed', '가격대 $10~25', '착용 방식 온이어 헤드폰 또는 오버이어 헤드폰'
    """
    attr = spec.attribute(attribute)
    sep = ": " if lang == "en" else " "
    if value == RANGE:
        shown = range_label(low, high, lang)
    elif value == ANY_OF:
        shown = t(lang, "or").join(value_label(attr, v, lang) for v in values or [])
    else:
        shown = value_label(attr, value, lang)
    return f"{attr.label_for(lang)}{sep}{shown}"


def build_question(spec: CategorySpec, attribute: str, text: str | None = None, lang: str = "ko") -> Question:
    """보기 버튼과 함께 보낼 질문. text가 없으면 카테고리 정의의 고정 질문."""
    attr = spec.attribute(attribute)
    options = [Option(value=v.value, label=v.label_for(lang)) for v in attr.values]
    options.append(Option(value=DONT_CARE, label=t(lang, "dont_care")))
    label = attr.label_for(lang)
    return Question(attribute=attr.key, label=label, text=text or attr.question_for(lang) or label,
                    options=options)


def intro(spec: CategorySpec, lang: str = "ko") -> str:
    return t(lang, "intro", name=spec.name_for(lang))


def non_shopping_reply(spec: CategorySpec, request_type: str, lang: str = "ko") -> str:
    key = request_type if request_type in ("unsafe", "other_product") else "off_topic"
    return t(lang, key, name=spec.name_for(lang))


def acknowledgement(learned: list[str], lang: str = "ko") -> str:
    """고정 문구일 때 이번 턴에 새로 알게 된 것 알림."""
    return t(lang, "ack", items=", ".join(learned)) + "\n" if learned else ""


# 추천 전 확인 질문의 보기 버튼 ("이대로 추천해 줘")에 쓰는 가짜 속성 이름
CONFIRM_ATTRIBUTE = "_confirm"


def confirmation(items: list[str], lang: str = "ko", last: bool = False) -> str:
    """추천 직전 확인 질문: 지금까지의 조건을 정리하고 이대로 찾을지 묻는다."""
    return t(lang, "confirm_last" if last else "confirm", items=", ".join(items))


def confirm_question(lang: str = "ko") -> Question:
    return Question(attribute=CONFIRM_ATTRIBUTE, label=t(lang, "confirm_label"), text=t(lang, "confirm_yes"),
                    options=[Option(value="yes", label=t(lang, "confirm_yes"))])


def guess_note(guesses: list[str], lang: str = "ko") -> str:
    """고정 문구일 때 이번 턴에 짐작한 것 알림 (아니면 바로잡게 한다)."""
    return t(lang, "guess", items=", ".join(guesses)) + "\n" if guesses else ""


# ---------------------------------------------------------------- 추천

def reasons_for(spec: CategorySpec, ranked: RankedItem, state: PreferenceState, lang: str = "ko") -> list[str]:
    reasons = []
    for key in ranked.matched:
        attr = spec.attribute(key)
        if key in state.ranges:  # 숫자 범위 조건: 말한 범위를 보여 준다
            reasons.append(f"{attr.label_for(lang)}: {range_label(*state.ranges[key], lang)}")
        elif key in state.any_of:  # 여러 값 조건: 이 상품의 실제 값을 보여 준다
            reasons.append(f"{attr.label_for(lang)}: {value_label(attr, ranked.item.attributes[key], lang)}")
        else:
            reasons.append(f"{attr.label_for(lang)}: {value_label(attr, state.explicit[key], lang)}")
    for key in ranked.unknown:
        reasons.append(t(lang, "reason.unknown", label=spec.attribute(key).label_for(lang)))
    for key in ranked.violated:
        attr = spec.attribute(key)
        number = ranked.item.numbers.get(key) if key in state.ranges else None
        actual = f"${number:g}" if number is not None else value_label(attr, ranked.item.attributes.get(key, ""), lang)
        reasons.append(t(lang, "reason.violated", label=attr.label_for(lang), value=actual))
    return reasons


def to_recommendation(spec: CategorySpec, product: Product, ranked: RankedItem,
                      state: PreferenceState, lang: str = "ko") -> Recommendation:
    return Recommendation(
        product_id=product.id, title=product.title, probability=ranked.probability,
        price=product.price, image_url=product.image_url,
        reasons=reasons_for(spec, ranked, state, lang),
    )


def describe_limitation(spec: CategorySpec, lim: Limitation, tie_basis: str | None = None,
                        lang: str = "ko") -> str:
    """tie_basis: 동률 정렬 기준 (cheaper / pricier / relevance / rating, chat.tie_order)"""
    if lim.code == "tied_candidates":
        basis = tie_basis if f"tie_order.{tie_basis}" in TEXT["ko"] else "rating"
        return t(lang, "tied", count=lim.detail["count"], order=t(lang, f"tie_order.{basis}"))
    if lim.code == "unknown_attributes":
        labels = ", ".join(spec.attribute(k).label_for(lang) for k in lim.detail)
        return t(lang, "unknown_attributes", labels=labels)
    if lim.code == "constraint_relaxed":
        return t(lang, "relaxed")
    return ""


def recommendation_reply(spec: CategorySpec, reason: str, limitations: list[Limitation],
                         tie_basis: str | None, learned: list[str], lang: str = "ko",
                         guessed: list[str] | None = None) -> str:
    stop = f"stop.{reason}" if f"stop.{reason}" in TEXT["ko"] else "stop.default"
    lines = [acknowledgement(learned, lang) + guess_note(guessed or [], lang) + t(lang, stop)]
    lines += [text for lim in limitations if (text := describe_limitation(spec, lim, tie_basis, lang))]
    lines.append(t(lang, "refine_hint"))
    return "\n".join(lines)
