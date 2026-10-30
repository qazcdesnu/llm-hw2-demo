"""LLM 파서: 사용자 발화 하나를 요청 분류·추천 조정과 속성 조건으로 바꾼다.

두 LLM 호출을 동시에 보낸다.
- 요청 분류기(이 파일): request_type, 추천 목록 조정(제외, 가격 정렬)
- 속성 추출기(extractor.py): 속성 조건과 사용 맥락 → 대화 메모리

출력 스키마와 프롬프트는 카테고리 정의(YAML)에서 만든다. 카테고리별 코드는 없다.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.catalog.spec import CategorySpec
from app.llm import LLMClient, LLMError
from app.services.extractor import (
    KRW_PER_USD, ExtractContext, ParsedConstraint, extract_attributes, validate_constraints,
)

logger = logging.getLogger(__name__)

__all__ = ["KRW_PER_USD", "LLMParser", "ParseContext", "ParseResult", "ParsedConstraint",
           "parse_message", "validate_constraints"]

RequestType = Literal["shopping", "other_product", "off_topic", "unsafe"]
PriceOrder = Literal["cheaper", "pricier", "none"]
REQUEST_TYPES: tuple[str, ...] = RequestType.__args__  # type: ignore[attr-defined]


@dataclass
class ParseContext:
    """발화를 해석할 때 함께 넘기는 대화 맥락.

    @see docs/llm-parser.md#입력
    """

    pending: str | None = None  # 지금 답을 기다리는 질문의 속성
    question_text: str | None = None  # 그 질문의 실제 문장
    known: list[str] = field(default_factory=list)  # 메모리 요약 (ConversationMemory.describe)
    current: list[str] | None = None  # 현재 추천 요약 (추천 후일 때)


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
    # 속성 밖의 사용 맥락 (메모리에 남긴다)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 요청 분류기

class RouteOutput(BaseModel):
    request_type: RequestType
    exclude_ranks: list[int] = Field(description="현재 추천 목록에서 빼 달라는 상품 번호(1부터). 없으면 빈 목록")
    price_order: PriceOrder = Field(description="'더 싼 걸로'=cheaper, '더 비싸도 좋은 걸로'=pricier, 그 외 none")


@lru_cache(maxsize=32)
def _instructions(name: str) -> str:
    lines = [
        f"너는 '{name}' 쇼핑 도우미의 요청 분류기다.",
        "사용자 발화를 (1) 요청 분류와 (2) 현재 추천 목록 조정으로 바꾼다. 상품 속성은 다른 모듈이 뽑는다.",
        "",
        "## request_type",
        "사용자 발화 자체만 보고 판단한다. 직전 질문·지금까지 알게 된 것·현재 추천 같은 맥락이 있어도",
        "발화가 위험하거나 무관하면 그대로 unsafe/off_topic/other_product로 분류한다.",
        f"- shopping: {name}을(를) 찾는 대화. 질문에 대한 답(상황 설명, 짧은 답, 모르겠다는 말 포함),",
        "  정정, 추천 조정, '추천해 줘', 쓰임새·취향 이야기도 포함",
        f"- other_product: {name}이(가) 아닌 다른 종류의 상품을 찾음 (예: 노트북, 스피커)",
        "- off_topic: 쇼핑과 무관한 대화 (예: 날씨, 잡담)",
        "- unsafe: 무기·총기·마약·폭발물 등 위험하거나 불법적인 것을 구하거나 만드는 요청",
        f"  단, {name}을(를) 찾는 것이면 위험한 단어가 있어도 shopping이다 (예: 사격장에서 쓸 헤드폰)",
        "",
        "## 추천 목록 조정 ('현재 추천'이 주어졌을 때만)",
        "- '더 싼 걸로', '저렴한 거'처럼 구체적 금액이 없는 상대 표현은 price_order=cheaper (pricier도 같음).",
        "- '1번 말고', '첫 번째는 빼고'는 exclude_ranks에 번호를 넣는다.",
        "- 현재 추천이 없으면 exclude_ranks=[], price_order=none.",
        "",
        "## 예시",
        '- "무선이고 노이즈 캔슬링 되는 걸로" → shopping',
        '- "지하철로 출퇴근할 때 쓸 거야" → shopping',
        '- (직전 질문 "주로 어디서 들어?") "음 잘 모르겠는데" → shopping',
        '- "노트북 추천해줘" → other_product',
        '- "오늘 날씨 어때?" → off_topic',
        '- "총 구하는 방법 알려줘" → unsafe',
        '- (현재 추천 있음) "좀 더 저렴한 걸로" → shopping, price_order=cheaper',
        '- (현재 추천 있음) "2번은 빼고 보여줘" → shopping, exclude_ranks=[2]',
        '- (지금까지 알게 된 것 있음, 현재 추천 있음) "총 구하는 방법 알려줘" → unsafe',
    ]
    return "\n".join(lines)


def build_instructions(spec: CategorySpec) -> str:
    """@docid chat.parser-instructions
    @see docs/llm-parser.md#입력
    """
    return _instructions(spec.name)


def build_input(message: str, ctx: ParseContext) -> str:
    parts = []
    if ctx.question_text:
        parts.append(f"직전 질문: {ctx.question_text}")
    if ctx.known:
        parts.append("지금까지 알게 된 것: " + "; ".join(ctx.known))
    if ctx.current:
        parts.append("현재 추천:\n" + "\n".join(f"{i}. {line}" for i, line in enumerate(ctx.current, 1)))
    parts.append(f"사용자 발화: {message}")
    return "\n".join(parts)


def classify_request(client: LLMClient, spec: CategorySpec, message: str,
                     ctx: ParseContext) -> RouteOutput:
    return client.parse([{"role": "user", "content": build_input(message, ctx)}], RouteOutput,
                        instructions=build_instructions(spec))


# ---------------------------------------------------------------- 실행

def parse_message(
    client: LLMClient,
    spec: CategorySpec,
    message: str,
    ctx: ParseContext | None = None,
) -> ParseResult:
    """발화 하나를 해석한다. 요청 분류와 속성 추출을 동시에 호출한다. 실패는 LLMError로 올린다.

    @docid chat.parse-message
    @see docs/architecture.md#1-한눈에-보기
    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/llm-parser.md#2-파서
    @see docs/llm-parser.md#llm-파서와-멀티턴-대화
    """
    ctx = ctx or ParseContext()
    extract_ctx = ExtractContext(ctx.pending, ctx.question_text, ctx.known)
    with ThreadPoolExecutor(max_workers=2) as pool:
        route_future = pool.submit(classify_request, client, spec, message, ctx)
        extraction_future = pool.submit(extract_attributes, client, spec, message, extract_ctx)
        route, extraction = route_future.result(), extraction_future.result()

    ranks = sorted({r for r in route.exclude_ranks if ctx.current and 1 <= r <= len(ctx.current)})
    order = route.price_order if ctx.current else "none"
    result = ParseResult(route.request_type, extraction.constraints, ranks, order,
                         extraction.rejected, extraction.notes)
    logger.info("parsed %r -> %s %s notes=%s exclude=%s order=%s rejected=%s", message,
                result.request_type,
                [(c.attribute, c.value) for c in result.constraints],
                result.notes, ranks, order, result.rejected)
    return result


class LLMParser:
    """대화 관리자가 쓰는 파서. 실패하면 None을 돌려 문자열 매칭으로 대신하게 한다."""

    def __init__(self, client: LLMClient):
        self.client = client

    def __call__(self, spec: CategorySpec, message: str, ctx: ParseContext) -> ParseResult | None:
        try:
            return parse_message(self.client, spec, message, ctx)
        except LLMError as e:
            logger.warning("LLM parse failed, falling back to text matching: %s", e)
            return None
