"""속성 추출기: 사용자 발화에서 카테고리 속성 값과 사용 맥락을 뽑는다 (요청 분류와 별도의 LLM 호출).

질문과 무관한 발화나 간접적인 답("주로 지하철에서 들어")에서도 채울 수 있는 속성을 모두 찾아
대화 메모리(memory.py)에 남긴다. 추출 결과는 그대로 믿는다(확신도를 받지 않는다). 그래서 지시는
"값이 분명히 따라 나올 때만 넣으라"고 하고, 틀린 값은 사용자가 대화로 바로잡는다.
출력 스키마와 프롬프트는 카테고리 정의(YAML)에서 만든다.
속성은 enum이라 없는 속성은 나올 수 없고, 값은 코드에서 정의와 대조해 맞지 않으면 버린다.

@see docs/briefing.md#4-아키텍처와-코드-지도
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.catalog.spec import CategorySpec
from app.core.model import DONT_CARE
from app.llm import LLMClient

# 원화 가격을 달러 구간에 맞추기 위한 환율 (프롬프트용 근사치)
KRW_PER_USD = 1400


@dataclass(frozen=True)
class ParsedConstraint:
    attribute: str
    value: str  # 보기 값 또는 DONT_CARE (자유값 속성은 소문자 문자열)
    quote: str = ""  # 근거가 된 사용자 발화


@dataclass
class Extraction:
    constraints: list[ParsedConstraint] = field(default_factory=list)
    # 속성으로 표현되지 않는 사용 맥락 (질문 작성기가 대화를 이어 갈 때 쓴다)
    notes: list[str] = field(default_factory=list)
    # 상품 설명 검색용 영어 키워드 (동률 정렬의 텍스트 관련도)
    keywords: list[str] = field(default_factory=list)
    # 정의와 맞지 않아 버린 출력 (디버그·평가용)
    rejected: list[str] = field(default_factory=list)


@dataclass
class ExtractContext:
    """추출에 필요한 대화 맥락."""

    pending: str | None = None  # 직전 질문이 알아내려던 속성
    question_text: str | None = None  # 직전 질문 문장 (돌려 물은 질문일 수 있다)
    known: list[str] = field(default_factory=list)  # 메모리 요약 (ConversationMemory.describe)


# ---------------------------------------------------------------- 스키마

@lru_cache(maxsize=32)
def _schema_for(spec_json: str) -> type[BaseModel]:
    spec = CategorySpec.model_validate_json(spec_json)
    keys = tuple(a.key for a in spec.attributes)
    constraint = create_model(
        f"{spec.id}_constraint",
        attribute=(Literal[keys], ...),  # type: ignore[valid-type]
        value=(str, Field(description="속성의 값 이름(영문) 또는 dont_care")),
        quote=(str, Field(description="근거가 된 사용자 발화 부분을 그대로 인용. 설명을 덧붙이지 않는다")),
    )
    return create_model(
        f"{spec.id}_extraction",
        constraints=(list[constraint], Field(description="발화에 근거가 있는 조건만. 없으면 빈 목록")),
        notes=(list[str], Field(description="속성 목록에 없지만 상품 선택에 도움이 되는 사용 맥락. 짧은 구절. 없으면 빈 목록")),
        keywords=(list[str], Field(description="상품 설명 검색용 영어 키워드. 없으면 빈 목록")),
    )


def schema_for(spec: CategorySpec) -> type[BaseModel]:
    """카테고리 정의로 만든 추출 출력 스키마. 정의가 같으면 캐시한다.

    @see docs/llm-parser.md#출력-구조화-출력-schema_for
    """
    return _schema_for(spec.model_dump_json())


# ---------------------------------------------------------------- 프롬프트

def build_instructions(spec: CategorySpec) -> str:
    """@see docs/llm-parser.md#입력"""
    lines = [
        f"너는 '{spec.name}' 쇼핑 도우미의 속성 추출기다.",
        "사용자 발화에서 아래 속성의 값을 채울 수 있는 근거를 모두 찾는다.",
        "도우미의 직전 질문과 무관한 말이어도, 채울 수 있는 속성이 있으면 모두 넣는다.",
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
        "## constraints 규칙",
        "넣은 값은 확인 없이 그대로 추천 조건이 된다. 그러니 값이 분명히 따라 나올 때만 넣는다.",
        "1. 이번 발화에 근거가 있는 속성만 넣는다. 언급되지 않은 속성은 절대 넣지 않는다.",
        "   '지금까지 알게 된 것'은 참고용이다. 이번 발화에서 새로 말하거나 바꾼 것만 넣는다.",
        "2. 값을 직접 말한 경우(부정 포함)뿐 아니라, 말한 상황·형편·습관에서 그 값이 분명히 따라 나오면 넣는다",
        "   (예: '학생이라 돈이 없어' → 가장 낮은 가격대, '헬스장에서 쓸' → 운동 용도). 이럴 수도 저럴 수도 있으면 넣지 않는다.",
        "   장소·환경만으로 기능이 필요하다고 짐작하지 않는다. 같은 곳에서 써도 원하는 게 다를 수 있다",
        "   ('지하철에서 들어' → 노이즈 캔슬링 필요 아님, '운동할 때 써' → 방수 필요 아님). 이런 상황은 notes에 적는다.",
        "   그 기능을 바란다는 말(소리를 막고 싶다, 땀에 젖어 고장 난 적이 있다)이 있을 때만 넣는다.",
        "3. value=dont_care는 사용자가 그 속성은 어느 쪽이든 괜찮다는 뜻을 밝혔을 때 쓴다. 특정 단어가 아니라 뜻으로 판단한다",
        "   ('둘 다 괜찮아', '그건 신경 안 써', '별로 안 중요해'). '모르겠다'는 상관없다는 뜻이 아니다.",
        f"4. 원화 가격은 1달러={KRW_PER_USD}원으로 환산해 가격대 값을 고른다.",
        "   직전 질문이 금액 범위를 어림해 보여 줬으면(예: '3만 원 안, 3만~8만 원, 그 이상') 그 범위는 순서대로",
        "   가격대 값과 같다. 사용자가 그중 하나를 고르거나 그 경계로 답하면 그 값을 넣는다.",
        "5. 직전 질문은 기능을 직접 묻지 않고 상황을 돌려 물었을 수 있다 (예: '주로 어디서 들어?').",
        "   짧은 답('응', '그거', '지하철')은 그 질문과 '질문 의도'의 속성에 비춰 해석한다.",
        "6. 이전 조건을 바꾸는 말(정정)은 새 값을 넣는다.",
        "7. '더 싼 걸로'처럼 금액 없는 상대 표현은 price_band에 넣지 않는다. 구체적 금액·가격대만 넣는다.",
        "8. quote에는 근거가 된 사용자 발화 부분만 그대로 옮긴다(예: '지하철에서 주로 들어'). 설명·추론을 쓰지 않는다.",
        "9. 여러 값에 걸치는 일반 명칭('헤드폰', '헤드셋', '이어폰 하나')만으로는 값을 정하지 않는다.",
        "   값의 뜻에 정확히 들어맞을 때만 넣는다(예: 사격장·공연장에서 쓰는 건 '운동' 용도가 아니다).",
        "",
        "## notes 규칙",
        "속성 목록으로 표현되지 않지만 다음 질문이나 추천에 쓸 만한 사용 맥락을 짧은 구절로 적는다",
        "(예: '지하철 출퇴근 1시간', '안경 착용', '음질 중시', '선물용'). 이미 알게 된 맥락은 다시 적지 않는다.",
        "사용자가 쓴 언어로 적는다.",
        "",
        "## keywords 규칙",
        "이번 발화에서 바라는 것을 영어 상품 설명에서 찾을 때 쓸 영어 키워드·짧은 구로 적는다.",
        "속성 값으로 넣은 것도, notes에 적은 것도, 기능 이름이 아닌 바람(오래 껴도 편함, 작은 귀에 맞음)도 포함한다",
        "(예: '하루 종일 껴도 편했으면' → ['comfortable', 'all day wear', 'lightweight'],",
        " '달릴 때 안 빠지게' → ['secure fit', 'running', 'ear hooks']). 근거가 없으면 빈 목록.",
        "",
        "## 예시",
        '- "무선이고 노이즈 캔슬링 되는 걸로" → [connection=wireless, anc=yes], notes []',
        '- "지하철로 출퇴근할 때 쓸 거야" → [], notes ["지하철 출퇴근"] (노캔을 바란다는 말은 없음)',
        '- "지하철에서 들을 때 시끄러워서 소리를 계속 키우게 돼" → [anc=yes], notes ["지하철 출퇴근"]',
        '- "헬스장에서 쓸 이어폰" → [use_case=sports, form_factor=in_ear], notes []',
        '- "노캔은 필요 없어" → [anc=no]',
        '- "가격은 상관없어" → [price_band=dont_care]',
        '- "헤드폰 추천해 줘" → []',
        '- (직전 질문 "주로 어디서 들어? 시끄러운 데서도 써?", 질문 의도 anc) "카페에서 공부할 때" → '
        '[], notes ["카페에서 공부"] (장소만 말함)',
        '- (직전 질문 "카페에서 공부할 때 주변 소리 싹 막히면 좋겠어, 좀 들리는 게 편해?", 질문 의도 anc) '
        '"싹 막히면 좋겠어" → [anc=yes]',
        '- (직전 질문 "주로 어디서 들어?", 질문 의도 anc) "집에서만 써" → [], notes ["집에서 사용"]',
        '- (직전 질문 "주로 어디서 들어?", 질문 의도 anc) "여기저기" → [] (값이 따라 나오지 않음)',
        '- (직전 질문 "선 있는 거 괜찮아?", 질문 의도 connection) "응 괜찮아" → [connection=dont_care]',
        '- (직전 질문 "운동할 때도 써?", 질문 의도 water_resistant) "근데 소니 거 괜찮아?" → [] (관심일 뿐 조건이 아님)',
    ]
    return "\n".join(lines)


def build_input(spec: CategorySpec, message: str, ctx: ExtractContext) -> str:
    parts = []
    if ctx.question_text:
        parts.append(f"직전 질문: {ctx.question_text}")
    if ctx.pending:
        parts.append(f"질문 의도: {ctx.pending} ({spec.attribute(ctx.pending).label})")
    if ctx.known:
        parts.append("지금까지 알게 된 것:\n" + "\n".join(f"- {line}" for line in ctx.known))
    parts.append(f"사용자 발화: {message}")
    return "\n".join(parts)


# ---------------------------------------------------------------- 실행

def validate_constraints(spec: CategorySpec, raw: list) -> tuple[list[ParsedConstraint], list[str]]:
    """정의에 맞는 조건만 남긴다. 같은 속성이 여러 번 나오면 마지막 것을 쓴다.

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
        kept[c.attribute] = ParsedConstraint(c.attribute, value, c.quote.strip())
    return list(kept.values()), rejected


def extract_attributes(client: LLMClient, spec: CategorySpec, message: str,
                       ctx: ExtractContext | None = None) -> Extraction:
    """발화 하나에서 속성 값과 맥락을 뽑는다. LLM 호출 실패는 LLMError로 올린다.

    @docid chat.extract-attributes
    @see docs/architecture.md#1-한눈에-보기
    @see docs/briefing.md#대화-한-턴
    @see docs/llm-parser.md#llm-파서와-멀티턴-대화
    """
    ctx = ctx or ExtractContext()
    raw = client.parse(
        [{"role": "user", "content": build_input(spec, message, ctx)}],
        schema_for(spec),
        instructions=build_instructions(spec),
    )
    constraints, rejected = validate_constraints(spec, raw.constraints)
    notes = [n.strip() for n in raw.notes if n.strip()]
    keywords = [k.strip() for k in raw.keywords if k.strip()]
    return Extraction(constraints, notes, rejected=rejected, keywords=keywords)
