"""LLM 파서: 사용자 발화를 카테고리 속성 조건과 요청 분류로 바꾼다.

출력 스키마와 프롬프트는 카테고리 정의(YAML)에서 만든다. 카테고리별 코드는 없다.
속성은 enum이라 없는 속성은 나올 수 없고, 값은 코드에서 정의와 대조해 맞지 않으면 버린다.
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.catalog.spec import CategorySpec
from app.core.model import DONT_CARE
from app.llm import LLMClient, LLMError

logger = logging.getLogger(__name__)

RequestType = Literal["shopping", "other_product", "off_topic", "unsafe"]
PriceOrder = Literal["cheaper", "pricier", "none"]
REQUEST_TYPES: tuple[str, ...] = RequestType.__args__  # type: ignore[attr-defined]

# 원화 가격을 달러 구간에 맞추기 위한 환율 (프롬프트용 근사치)
KRW_PER_USD = 1400


@dataclass(frozen=True)
class ParsedConstraint:
    attribute: str
    value: str  # 보기 값 또는 DONT_CARE (자유값 속성은 소문자 문자열)
    kind: Literal["explicit", "inferred"]
    confidence: float
    reason: str = ""


@dataclass
class ParseResult:
    request_type: str
    constraints: list[ParsedConstraint] = field(default_factory=list)
    # 현재 추천 목록에서 빼 달라고 한 순위 (1부터)
    exclude_ranks: list[int] = field(default_factory=list)
    # "더 싼/비싼 걸로": 조건이 똑같이 맞는 상품들 사이의 가격 정렬 방향
    price_order: str = "none"
    # 정의와 맞지 않아 버린 출력 (디버그·평가용)
    rejected: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 스키마

@lru_cache(maxsize=32)
def _schema_for(spec_json: str) -> type[BaseModel]:
    spec = CategorySpec.model_validate_json(spec_json)
    keys = tuple(a.key for a in spec.attributes)
    constraint = create_model(
        f"{spec.id}_constraint",
        attribute=(Literal[keys], ...),  # type: ignore[valid-type]
        value=(str, Field(description="속성의 값 이름(영문) 또는 dont_care")),
        kind=(Literal["explicit", "inferred"], ...),
        confidence=(float, Field(description="inferred일 때 그 값이 실제로 필요할 확률 0~1. explicit이면 1")),
        reason=(str, Field(description="근거가 된 사용자 표현")),
    )
    return create_model(
        f"{spec.id}_parse",
        request_type=(RequestType, ...),
        constraints=(list[constraint], Field(description="발화에 근거가 있는 조건만. 없으면 빈 목록")),
        exclude_ranks=(list[int], Field(description="현재 추천 목록에서 빼 달라는 상품 번호(1부터). 없으면 빈 목록")),
        price_order=(PriceOrder, Field(description="'더 싼 걸로'=cheaper, '더 비싸도 좋은 걸로'=pricier, 그 외 none")),
    )


def schema_for(spec: CategorySpec) -> type[BaseModel]:
    """카테고리 정의로 만든 구조화 출력 스키마. 정의가 같으면 캐시한다.

    @see docs/llm-parser.md#출력-구조화-출력-schema_for
    """
    return _schema_for(spec.model_dump_json())


# ---------------------------------------------------------------- 프롬프트

def build_instructions(spec: CategorySpec) -> str:
    """@docid chat.parser-instructions
    @see docs/llm-parser.md#입력
    """
    lines = [
        f"너는 '{spec.name}' 쇼핑 도우미의 입력 해석기다.",
        "사용자 발화를 (1) 요청 분류와 (2) 속성 조건 목록으로 바꾼다.",
        "",
        "## 속성과 값 (value에는 괄호 앞의 영문 이름을 쓴다)",
    ]
    for a in spec.attributes:
        if a.is_open:
            lines.append(f"- {a.key} ({a.label}): 영문 소문자 이름 (예: 소니 → sony)")
        else:
            options = ", ".join(f"{v.value}({v.label})" for v in a.values)
            lines.append(f"- {a.key} ({a.label}): {options}")
    lines += [
        "",
        "## request_type",
        "사용자 발화 자체만 보고 판단한다. 현재 질문·지금까지 조건·현재 추천 같은 맥락이 있어도",
        "발화가 위험하거나 무관하면 그대로 unsafe/off_topic/other_product로 분류한다.",
        f"- shopping: {spec.name}을(를) 찾는 대화. 조건 답변, 정정, 추천 조정, '추천해 줘'도 포함",
        f"- other_product: {spec.name}이(가) 아닌 다른 종류의 상품을 찾음 (예: 노트북, 스피커)",
        "- off_topic: 쇼핑과 무관한 대화 (예: 날씨, 잡담)",
        "- unsafe: 무기·총기·마약·폭발물 등 위험하거나 불법적인 것을 구하거나 만드는 요청",
        f"  단, {spec.name}을(를) 찾는 것이면 위험한 단어가 있어도 shopping이다 (예: 사격장에서 쓸 헤드폰)",
        "",
        "## constraints 규칙",
        "1. 이번 발화에 근거가 있는 속성만 넣는다. 언급되지 않은 속성은 절대 넣지 않는다.",
        "   '지금까지 조건'은 참고용이다. 이번 발화에서 새로 말하거나 바꾼 것만 넣고, 이미 있는 조건을 다시 넣지 않는다.",
        "2. 사용자가 그 속성을 직접 말하면 kind=explicit, confidence=1. 부정도 explicit이다.",
        "3. value=dont_care는 사용자가 그 속성이 '상관없다/아무거나'라고 직접 말했을 때만 쓴다.",
        "4. 직접 말하지 않았지만 상황상 그 값이 필요할 가능성이 높으면 kind=inferred로 넣고,",
        "   confidence에 그 가능성(0.5~0.9)을 준다. 근거가 약하면 넣지 않는다.",
        f"5. 원화 가격은 1달러={KRW_PER_USD}원으로 환산해 가격대 값을 고른다.",
        "6. '현재 질문'이 있으면 짧은 답('응', '그거', '귀에 넣는 거')은 그 질문의 속성에 대한 답이다.",
        "7. 이전 조건을 바꾸는 말(정정)은 새 값을 explicit으로 넣는다.",
        "8. '현재 추천'이 주어지면 추천 목록을 조정하는 말로 해석한다.",
        "   '더 싼 걸로', '저렴한 거'처럼 구체적 금액이 없는 상대 표현은 price_order=cheaper (pricier도 같음).",
        "   price_band는 구체적인 금액·가격대를 말했을 때만 넣는다.",
        "   '1번 말고', '첫 번째는 빼고'는 exclude_ranks에 번호를 넣는다.",
        "",
        "## 예시",
        '- "무선이고 노이즈 캔슬링 되는 걸로" → shopping, '
        "[connection=wireless explicit, anc=yes explicit]",
        '- "지하철로 출퇴근할 때 쓸 거야" → shopping, '
        "[anc=yes inferred 0.7, connection=wireless inferred 0.7]",
        '- "헬스장에서 쓸 이어폰" → shopping, '
        "[use_case=sports explicit, form_factor=in_ear explicit, water_resistant=yes inferred 0.6]",
        '- "노캔은 필요 없어" → shopping, [anc=no explicit]',
        '- "가격은 상관없어" → shopping, [price_band=dont_care explicit]',
        '- "헤드폰 추천해 줘" → shopping, []',
        '- "노트북 추천해줘" → other_product, []',
        '- "총 구하는 방법 알려줘" → unsafe, []',
        '- (현재 추천 있음) "좀 더 저렴한 걸로" → shopping, [], price_order=cheaper',
        '- (현재 추천 있음) "2번은 빼고 보여줘" → shopping, [], exclude_ranks=[2]',
        '- (지금까지 조건 connection=wireless, 현재 추천 있음) "총 구하는 방법 알려줘" → unsafe, []',
        '- (지금까지 조건 connection=wireless) "노캔도 있었으면" → shopping, [anc=yes explicit] (connection은 다시 넣지 않음)',
    ]
    return "\n".join(lines)


def build_input(message: str, pending_label: str | None, known: dict[str, str],
                current: list[str] | None = None) -> str:
    parts = []
    if pending_label:
        parts.append(f"현재 질문: {pending_label}")
    if known:
        parts.append("지금까지 조건: " + ", ".join(f"{k}={v}" for k, v in known.items()))
    if current:
        parts.append("현재 추천:\n" + "\n".join(f"{i}. {line}" for i, line in enumerate(current, 1)))
    parts.append(f"사용자 발화: {message}")
    return "\n".join(parts)


# ---------------------------------------------------------------- 실행

def validate_constraints(spec: CategorySpec, raw: list) -> tuple[list[ParsedConstraint], list[str]]:
    """정의에 맞는 조건만 남긴다. 같은 속성이 여러 번 나오면 explicit, 그다음 마지막 것을 쓴다.

    @see docs/llm-parser.md#출력-구조화-출력-schema_for
    """
    kept: dict[str, ParsedConstraint] = {}
    rejected = []
    for c in raw:
        attr = spec.attribute(c.attribute)
        value = c.value.strip()
        if attr.is_open:
            value = value.casefold()
        elif value != DONT_CARE and value not in {v.value for v in attr.values}:
            rejected.append(f"{c.attribute}={value}")
            continue
        if not value:
            continue
        confidence = 1.0 if c.kind == "explicit" else min(max(float(c.confidence), 0.0), 1.0)
        parsed = ParsedConstraint(c.attribute, value, c.kind, confidence, c.reason)
        previous = kept.get(c.attribute)
        if previous is None or previous.kind == "inferred" or parsed.kind == "explicit":
            kept[c.attribute] = parsed
    return list(kept.values()), rejected


def parse_message(
    client: LLMClient,
    spec: CategorySpec,
    message: str,
    pending: str | None = None,
    known: dict[str, str] | None = None,
    current: list[str] | None = None,
) -> ParseResult:
    """발화 하나를 해석한다. LLM 호출 실패는 LLMError로 올린다.

    @docid chat.parse-message
    @see docs/architecture.md#1-한눈에-보기
    @see docs/llm-parser.md#llm-파서와-멀티턴-대화
    """
    pending_label = spec.attribute(pending).label if pending else None
    parsed = client.parse(
        [{"role": "user", "content": build_input(message, pending_label, known or {}, current)}],
        schema_for(spec),
        instructions=build_instructions(spec),
    )
    constraints, rejected = validate_constraints(spec, parsed.constraints)
    ranks = sorted({r for r in parsed.exclude_ranks if current and 1 <= r <= len(current)})
    result = ParseResult(parsed.request_type, constraints, ranks, parsed.price_order, rejected)
    logger.info("parsed %r -> %s %s exclude=%s order=%s rejected=%s", message, result.request_type,
                [(c.attribute, c.value, c.kind, c.confidence) for c in constraints],
                ranks, result.price_order, rejected)
    return result


class LLMParser:
    """대화 관리자가 쓰는 파서. 실패하면 None을 돌려 문자열 매칭으로 대신하게 한다."""

    def __init__(self, client: LLMClient):
        self.client = client

    def __call__(self, spec: CategorySpec, message: str, pending: str | None,
                 known: dict[str, str], current: list[str] | None = None) -> ParseResult | None:
        try:
            return parse_message(self.client, spec, message, pending, known, current)
        except LLMError as e:
            logger.warning("LLM parse failed, falling back to text matching: %s", e)
            return None
