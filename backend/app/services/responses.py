"""응답 표현: 엔진의 결정을 사용자에게 보여줄 말과 데이터로 바꾼다.

질문 문장은 질문 작성기(questioner.py, LLM)가 만들고, 실패하면 카테고리 정의의 고정 질문을 쓴다.
추천 안내·한계·거절 문구와 추천 근거는 이 파일의 템플릿이다. 말투는 친한 친구의 반말로 맞춘다.

@see docs/architecture.md#1-한눈에-보기
@see docs/architecture.md#3-대화-한-턴의-흐름
"""

from app.catalog.repository import Product
from app.catalog.spec import AttributeSpec, CategorySpec
from app.core.engine import Limitation
from app.core.model import DONT_CARE, PreferenceState
from app.core.recommender import RankedItem
from app.schemas import Option, Question, Recommendation

DONT_CARE_LABEL = "상관없음"

STOP_MESSAGES = {
    "budget": "말해 준 조건으로 골라 봤어.",
    "confident": "조건에 딱 맞는 걸 찾았어!",
    "no_informative_question": "더 물어봐도 후보가 크게 안 줄어서, 지금 조건으로 골라 봤어.",
    "refined": "말한 거 반영해서 다시 골라 봤어.",
}
REFINE_HINT = "더 고려할 점이나 바꾸고 싶은 게 있으면 말해 줘. (예: 더 저렴한 걸로, 1번 말고)"
NOT_UNDERSTOOD = "음, 잘 못 알아들었어. 아래 보기에서 골라 줄래?"
UNCLEAR_PREFIX = "음, 그건 잘 모르겠어."
REFINE_UNCLEAR = "어떤 걸 바꾸고 싶은지 조금만 더 말해 줄래?"
REFINE_NEEDS_LLM = "추가 조건을 알아들으려면 LLM 연결이 필요해. 새 대화로 다시 찾을 수 있어."
TIE_ORDER_TEXT = {"cheaper": "가격이 낮은 순", "pricier": "가격이 높은 순"}


def value_label(attr: AttributeSpec, value: str) -> str:
    if value == DONT_CARE:
        return DONT_CARE_LABEL
    return next((v.label for v in attr.values if v.value == value), value)


def build_question(spec: CategorySpec, attribute: str, text: str | None = None) -> Question:
    """보기 버튼과 함께 보낼 질문. text가 없으면 카테고리 정의의 고정 질문."""
    attr = spec.attribute(attribute)
    options = [Option(value=v.value, label=v.label) for v in attr.values]
    options.append(Option(value=DONT_CARE, label=DONT_CARE_LABEL))
    return Question(attribute=attr.key, label=attr.label, text=text or attr.question or attr.label,
                    options=options)


def intro(spec: CategorySpec) -> str:
    return f"{spec.name} 고르는 거 도와줄게! 몇 가지만 물어볼게."


def non_shopping_reply(spec: CategorySpec, request_type: str) -> str:
    if request_type == "unsafe":
        return f"미안, 그건 도와줄 수 없어. {spec.name} 찾는 건 계속 도와줄게."
    if request_type == "other_product":
        return f"지금은 {spec.name}만 추천할 수 있어."
    return f"나는 {spec.name} 고르는 걸 도와주고 있어."


def acknowledgement(learned: list[str]) -> str:
    """고정 문구일 때 이번 턴에 새로 알게 된 것 알림."""
    return f"반영했어: {', '.join(learned)}.\n" if learned else ""


# ---------------------------------------------------------------- 추천

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
        reasons.append(f"{attr.label}: {value_label(attr, actual)} (말한 조건과 다름)")
    return reasons


def to_recommendation(spec: CategorySpec, product: Product, ranked: RankedItem,
                      state: PreferenceState) -> Recommendation:
    return Recommendation(
        product_id=product.id, title=product.title, probability=ranked.probability,
        price=product.price, image_url=product.image_url,
        reasons=reasons_for(spec, ranked, state),
    )


def describe_limitation(spec: CategorySpec, lim: Limitation, price_order: str | None = None) -> str:
    if lim.code == "tied_candidates":
        order = TIE_ORDER_TEXT.get(price_order or "", "평점이 좋은 순")
        return (f"말해 준 조건으로는 {lim.detail['count']}개가 똑같이 잘 맞아서, "
                f"{order}으로 골랐어.")
    if lim.code == "unknown_attributes":
        labels = ", ".join(spec.attribute(k).label for k in lim.detail)
        return f"{labels}은(는) 일부 상품 정보에 없어서 확인 못 했어."
    if lim.code == "constraint_relaxed":
        return "조건을 모두 만족하는 상품이 부족해서, 일부 조건이 다른 것도 같이 보여 줄게."
    return ""


def recommendation_reply(spec: CategorySpec, reason: str, limitations: list[Limitation],
                         price_order: str | None, learned: list[str]) -> str:
    lines = [acknowledgement(learned) + STOP_MESSAGES.get(reason, "추천 상품이야.")]
    lines += [text for lim in limitations if (text := describe_limitation(spec, lim, price_order))]
    lines.append(REFINE_HINT)
    return "\n".join(lines)
