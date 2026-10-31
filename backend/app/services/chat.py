"""대화 관리자: 한 턴을 해석 → 상태 갱신 → 결정 → 표현 순서로 처리한다.

- 해석: 버튼 답(answer) 또는 LLM 파서(요청 분류 + 속성 추출). LLM이 없거나 실패하면 보기 이름 매칭.
- 상태 갱신: 알아낸 사실을 메모리에 남기고 질문 기록을 닫는다. 엔진 입력(PreferenceState)은 저장하지 않고
  메모리에서 만든다(state.py).
- 결정: IG 엔진이 다음 질문 또는 추천을 정한다.
- 표현: 질문은 질문 작성기(친구 말투, 실패하면 고정 질문), 추천·안내는 템플릿(responses.py).
추천을 보여준 뒤에도 대화가 이어져, 조건 추가·변경·상품 제외로 추천 목록을 조정한다.

@see docs/briefing.md#4-아키텍처와-코드-지도
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.catalog.repository import Product
from app.catalog.spec import AttributeSpec, CategorySpec
from app.core.adapters import from_catalog
from app.core.engine import Ask, Decision, EngineConfig, IGEngine, Recommend
from app.core.model import DONT_CARE, PreferenceState
from app.core.question_selector import QuestionPolicy
from app.core.recommender import TieKey
from app.schemas import DebugInfo, Question, QuestionScore, Recommendation
from app.services import responses as say
from app.services.memory import Fact
from app.services.parser import ParseContext, ParseResult
from app.services.questioner import QuestionRequest, WrittenQuestion
from app.services.relevance import Embedder, TextIndex, relevance_scores
from app.services.state import ChatState

DEBUG_TOP_N = 5

# (spec, 발화, 맥락) -> 해석 결과 (실패 시 None)
Parser = Callable[[CategorySpec, str, ParseContext], ParseResult | None]
# (spec, 질문 요청) -> 질문 문장과 접근 방식 (실패 시 None)
QuestionWriter = Callable[[CategorySpec, QuestionRequest], WrittenQuestion | None]


class InvalidAnswer(ValueError):
    """정의에 없는 속성이나 값으로 답한 경우."""


@dataclass
class Catalog:
    """한 카테고리의 정의, 상품, 엔진."""

    spec: CategorySpec
    products: dict[str, Product]
    engine: IGEngine
    # 동률 정렬의 텍스트 관련도 (없으면 조건이 아닌 속성 → 평점순으로만 정렬)
    text: TextIndex | None = None
    embed: Embedder | None = None

    def preference(self, state: ChatState) -> PreferenceState:
        """엔진 입력. 조건이 아닌 속성(constraint: false)의 사실은 넣지 않는다."""
        return state.preference(self.engine.attributes)


def build_catalog(
    spec: CategorySpec, products: Sequence[Product], config: EngineConfig = EngineConfig(),
    policy: QuestionPolicy | None = None, text: TextIndex | None = None, embed: Embedder | None = None,
) -> Catalog:
    """policy: 질문 선택 정책 (기본 IG. 평가의 비교군은 RandomPolicy)."""
    attributes, items = from_catalog(spec, products)
    return Catalog(spec, {p.id: p for p in products}, IGEngine(items, attributes, config, policy),
                   text, embed)


@dataclass
class TurnResult:
    reply: str
    state: ChatState
    debug: DebugInfo
    question: Question | None = None
    recommendations: list[Recommendation] = field(default_factory=list)


# ---------------------------------------------------------------- 입력 확인 (LLM 없을 때의 대체 경로)

def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.casefold()) if len(t) >= 2]


def match_text(attr: AttributeSpec, text: str) -> str | None:
    """보기 이름·값과 문자열로 맞춰 본다. 하나로 정해지지 않으면 None.

    LLM 파서가 없거나 호출이 실패했을 때만 쓰는 대체 경로다. LLM이 있으면 쓰지 않는다(판정은 LLM이 한다).
    "상관없음" 같은 의미 판정은 하지 않는다(대체 경로에서는 보기 버튼으로 고른다).
    "불필요"처럼 다른 보기("필요")를 포함하는 표현은 더 긴 쪽만 인정한다.

    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/llm-parser.md#1-입력-해석-순서
    """
    normalized = text.casefold()
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


# ---------------------------------------------------------------- 상태 갱신

def remember(state: ChatState, spec: CategorySpec, attribute: str, value: str, quote: str,
             source: str = "said") -> str | None:
    """사실을 메모리에 남긴다. 새로 알게 된 것이면 사용자에게 알릴 문구, 이미 알던 값이면 None."""
    if state.memory.remember(Fact(attribute, value, quote, state.turn, source)):
        return say.change_label(spec, attribute, value, state.language)
    return None


def apply_parsed(state: ChatState, parsed: ParseResult, spec: CategorySpec) -> list[str]:
    """해석 결과를 메모리와 추천 조정 상태에 반영하고, 새로 바뀐 것을 사람이 읽을 문구로 돌려준다.

    속성 추출 결과는 그대로 믿는다(확신도 없음). 틀렸으면 사용자가 대화로 정정한다.

    @docid chat.apply-parsed
    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/llm-parser.md#7-해석-결과를-대화-상태에-반영
    """
    changes = []
    state.memory.add_notes(parsed.notes)
    state.memory.add_keywords(parsed.keywords)
    for c in parsed.constraints:
        if (label := remember(state, spec, c.attribute, c.value, c.quote)) is not None:
            changes.append(label)
    for rank in parsed.exclude_ranks:
        if rank <= len(state.last_recommended):
            item_id = state.last_recommended[rank - 1]
            if item_id not in state.excluded:
                state.excluded.append(item_id)
            changes.append(say.t(state.language, "excluded", rank=rank))
    if parsed.price_order in ("cheaper", "pricier"):
        state.price_order = parsed.price_order
        changes.append(say.t(state.language, f"order.{parsed.price_order}"))
    return changes


def tie_order(catalog: Catalog, state: ChatState, use_text: bool = True) -> tuple[TieKey, str]:
    """조건이 똑같이 맞는(확률이 같은) 상품 사이의 순서와, 그 순서를 사용자에게 설명할 기준.

    1. 사용자가 요청한 가격순 ("더 싼 걸로")
    2. 조건이 아닌 속성(예: 주 용도)이 말한 값과 맞는 상품
    3. 대화와 상품 설명의 텍스트 관련도 (BM25 + 임베딩, RRF)
    4. 평점(인기도)
    텍스트 관련도는 질의 임베딩 호출이 들어서, 추천할 때만 이 함수를 부른다(decide).
    use_text=False면 3을 뺀다 (평가에서 텍스트 관련도의 효과를 따로 잴 때).

    @docid chat.tie-order
    @see backend/README.md#구조
    @see docs/architecture.md#1-한눈에-보기
    @see docs/briefing.md#ig-엔진-ig-enginemd
    @see docs/ig-engine.md#4-추천과-한계-표시
    """
    order = state.price_order if state.price_order in ("cheaper", "pricier") else None
    sign = 1 if order == "cheaper" else -1
    soft = {f.attribute: f.value for f in state.memory.latest().values()
            if not catalog.spec.attribute(f.attribute).constraint and f.value != DONT_CARE}
    relevance: dict[str, float] = {}
    if use_text and catalog.text is not None:
        relevance = relevance_scores(catalog.text, " ".join(state.memory.keywords),
                                     state.memory.search_text(), catalog.embed)

    def key(item) -> tuple:
        product = catalog.products[item.id]
        price = 0.0 if order is None else (
            float("inf") if product.price is None else sign * product.price)
        mismatch = sum(product.attributes.get(a) != v for a, v in soft.items())
        return (price, mismatch, -relevance.get(item.id, 0.0), -item.score)

    basis = order or ("relevance" if relevance or soft else "rating")
    return key, basis


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
            f"{spec.attribute(k).label}={say.value_label(spec.attribute(k), v)}"
            for k, v in product.attributes.items() if k != "brand"
        )
        price = f"${product.price:.2f}" if product.price is not None else "가격 정보 없음"
        lines.append(f"{product.title[:60]} | {price} | {attrs}")
    return lines


# ---------------------------------------------------------------- 결정·표현

def decide(catalog: Catalog, state: ChatState, pref: PreferenceState) -> tuple[Decision, str | None]:
    """엔진의 결정과, 추천이면 동률 정렬 기준(tie_order의 basis).

    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/ig-engine.md#4-추천과-한계-표시
    """
    if state.finished:
        key, basis = tie_order(catalog, state)
        return catalog.engine.recommend_now(pref, tie_key=key), basis
    decision = catalog.engine.step(pref)
    if isinstance(decision.action, Recommend):  # 추천할 때만 동률 정렬(텍스트 관련도)을 계산한다
        key, basis = tie_order(catalog, state)
        return catalog.engine.recommend_now(pref, decision.action.reason, key), basis
    return decision, None


def build_debug(catalog: Catalog, decision: Decision, state: ChatState, pref: PreferenceState,
                request_type: str | None = None) -> DebugInfo:
    spec, action = catalog.spec, decision.action
    memory = state.memory.to_dict()
    debug = DebugInfo(
        turn=state.turn,
        entropy=decision.entropy,
        top_candidates=[say.to_recommendation(spec, catalog.products[r.item.id], r, pref)
                        for r in decision.top[:DEBUG_TOP_N]],
        preferences=pref.to_dict(),
        request_type=request_type,
        memory={"facts": memory["facts"], "notes": memory["notes"],
                "questions": [{"attribute": q.attribute, "attempts": q.attempts, "status": q.status}
                              for q in state.questions.values()]},
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


def _same_question(catalog: Catalog, state: ChatState, prefix: str,
                   request_type: str | None = None) -> TurnResult:
    """상태를 바꾸지 않고 안내 문구와 함께 대기 중인 질문을 다시 보여준다 (질문이 없으면 문구만)."""
    pref = catalog.preference(state)
    debug = build_debug(catalog, decide(catalog, state, pref)[0], state, pref, request_type)
    pending = state.pending
    if pending is None:
        hint = f"\n{say.t(state.language, 'refine_hint')}" if state.finished else ""
        return TurnResult(prefix + hint, state, debug)
    question = say.build_question(catalog.spec, pending.attribute, pending.text, state.language)
    return TurnResult(f"{prefix}\n\n{question.text}", state, debug, question)


# ---------------------------------------------------------------- 한 턴

@dataclass
class _Understanding:
    """해석·상태 갱신의 결과."""

    request_type: str | None = None
    changes: list[str] = field(default_factory=list)  # 이번 턴에 새로 바뀐 것 (사람이 읽는 문구)
    unclear: bool = False  # 해석은 했지만 알아들은 것이 없음 → 다음 질문을 다르게 묻는다


def handle_message(
    state: ChatState, message: str, catalog: Catalog,
    answer: tuple[str, str] | None = None,
    parser: Parser | None = None,
    writer: QuestionWriter | None = None,
    history: list[dict[str, str]] | None = None,
) -> TurnResult:
    """대화 한 턴을 처리한다. history는 이번 발화 전까지의 최근 대화(질문 작성기 맥락).

    @docid chat.handle-turn
    @see backend/README.md#구조
    @see docs/architecture.md#1-한눈에-보기
    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/briefing.md#대화-한-턴
    @see docs/doc-links.md#3-docid
    @see docs/llm-parser.md#llm-파서와-멀티턴-대화
    """
    spec = catalog.spec
    first_turn = not state.finished and not state.questions and not state.memory.facts
    state.turn += 1

    # 1~2. 해석과 상태 갱신 (더 진행할 수 없으면 안내 응답으로 끝낸다)
    understood = _understand(state, message, catalog, answer, parser, writer)
    if isinstance(understood, TurnResult):
        return understood

    # 3. 결정
    pref = catalog.preference(state)
    decision, basis = decide(catalog, state, pref)
    debug = build_debug(catalog, decision, state, pref, understood.request_type)
    debug.tie_order = basis
    action = decision.action

    # 4. 표현 (사용자 발화의 언어로)
    lang = state.language
    if isinstance(action, Ask):
        written = None
        if writer is not None:
            written = writer(spec, QuestionRequest(
                attribute=action.attribute, message=message, first_turn=first_turn,
                retry=action.attribute in state.questions, unclear=understood.unclear,
                learned=understood.changes, known=state.memory.describe(spec),
                history=list(history or []), language=lang,
            ))
        if written is not None:
            question = say.build_question(spec, action.attribute, written.text, lang)
            reply = written.text
            debug.question_source, debug.question_approach = "llm", written.approach
        else:
            question = say.build_question(spec, action.attribute, lang=lang)
            prefix = f"{say.intro(spec, lang)}\n\n" if first_turn else ""
            if understood.unclear and not understood.changes:
                prefix += f"{say.t(lang, 'unclear_prefix')}\n"
            reply = prefix + say.acknowledgement(understood.changes, lang) + question.text
            debug.question_source = "template"
        state.ask(action.attribute, question.text)
        return TurnResult(reply, state, debug, question)

    assert isinstance(action, Recommend)
    state.finished = True
    state.last_recommended = [r.item.id for r in action.items]
    return TurnResult(
        say.recommendation_reply(spec, action.reason, action.limitations, basis,
                                 understood.changes, lang),
        state, debug,
        recommendations=[say.to_recommendation(spec, catalog.products[r.item.id], r, pref, lang)
                         for r in action.items],
    )


def _understand(
    state: ChatState, message: str, catalog: Catalog,
    answer: tuple[str, str] | None, parser: Parser | None, writer: QuestionWriter | None,
) -> _Understanding | TurnResult:
    """발화를 해석해 메모리·질문 기록에 반영한다. 더 진행하지 않을 때는 응답(TurnResult)을 돌려준다.

    | 입력 | 대기 질문 처리 |
    |---|---|
    | 보기 버튼 | 그 질문의 답 |
    | LLM: 위험·다른 상품 | 상태 그대로, 같은 질문 다시 |
    | LLM: 잡담 | 질문 단계면 답 안 함으로 닫고 대화를 이어 감 |
    | LLM: 쇼핑 | 대기 질문의 속성이 나왔으면 답, 아니면 답 안 함으로 닫음 |
    | LLM 없음·실패 | 보기 이름 매칭, 실패하면 같은 질문 다시 |
    """
    spec = catalog.spec
    pending = state.pending

    if answer is not None:
        validate_answer(spec, *answer)
        label = remember(state, spec, *answer, quote=message, source="button")
        if pending is not None:  # 예전 질문의 버튼이면 대기 질문은 답 안 함으로 닫는다
            state.close_pending(answered=pending.attribute == answer[0])
        return _Understanding(changes=[label] if label else [])

    parsed = None
    if parser is not None:
        ctx = ParseContext(pending.attribute if pending else None, pending.text if pending else None,
                           state.memory.describe(spec), recommendation_context(catalog, state))
        parsed = parser(spec, message, ctx)

    if parsed is None:  # LLM 없음·실패: 보기 이름 매칭만
        if state.finished:
            return _same_question(catalog, state, say.t(state.language, "refine_needs_llm"))
        if pending is None:
            return _Understanding()
        value = match_text(spec.attribute(pending.attribute), message)
        if value is None:
            return _same_question(catalog, state, say.t(state.language, "not_understood"))
        label = remember(state, spec, pending.attribute, value, message)
        state.close_pending(answered=True)
        return _Understanding(changes=[label] if label else [])

    request_type = parsed.request_type
    state.language = parsed.language
    if request_type in ("unsafe", "other_product") or (
            request_type == "off_topic" and (writer is None or state.finished)):
        return _same_question(catalog, state, say.non_shopping_reply(spec, request_type, state.language),
                              request_type)
    if request_type == "off_topic":
        state.close_pending(answered=False)
        return _Understanding(request_type, unclear=True)

    changes = apply_parsed(state, parsed, spec)
    said = {c.attribute for c in parsed.constraints}
    if state.finished and not (said or changes):
        return _same_question(catalog, state, say.t(state.language, "refine_unclear"), request_type)
    if pending is not None:
        state.close_pending(answered=pending.attribute in said)
    return _Understanding(request_type, changes, unclear=not (said or changes))
