"""LLM 파서(요청 분류 + 속성 추출)와 질문 작성기. 가짜 LLM 클라이언트로 네트워크 없이 확인한다."""

import threading

import pytest

from app.catalog.spec import load_category_spec
from app.llm import LLMError
from app.services.extractor import ExtractContext, build_input as extract_input, schema_for
from app.services.parser import LLMParser, ParseContext, RouteOutput, parse_message
from app.services.questioner import (
    LLMQuestionWriter, QuestionDraft, QuestionRequest, WrittenQuestion, build_input as question_input,
    build_instructions,
)

SPEC = load_category_spec("headphones")
Extraction = schema_for(SPEC)
Constraint = Extraction.model_fields["constraints"].annotation.__args__[0]


class FakeClient:
    """스키마 종류에 따라 미리 정한 응답. 호출 스레드와 입력을 기록한다."""

    def __init__(self, route, extraction, text="질문", fail=False, barrier=None):
        self.route, self.extraction, self.text, self.fail = route, extraction, text, fail
        self.barrier = barrier  # 주면 두 호출이 동시에 들어와야 통과한다
        self.calls = []

    def parse(self, messages, schema, *, instructions=None):
        self.calls.append((schema, messages[0]["content"]))
        if self.barrier:
            self.barrier.wait()
        if self.fail:
            raise LLMError("boom")
        if schema is QuestionDraft:
            return QuestionDraft(approach="출퇴근 장면", text=self.text)
        return self.route if schema is RouteOutput else self.extraction


def route(request_type="shopping", exclude=(), order="none", language="ko"):
    return RouteOutput(request_type=request_type, language=language, exclude_ranks=list(exclude),
                       price_order=order)


def test_parse_message_merges_router_and_extractor_calls():
    extraction = Extraction(constraints=[
        Constraint(attribute="anc", value="yes", quote=" 지하철 "),
        Constraint(attribute="connection", value="bluetooth", quote="x"),
    ], notes=["지하철 출퇴근", " "], keywords=["commute", " "])
    # 두 호출이 동시에 나가지 않으면 barrier에서 시간 초과(BrokenBarrierError)
    client = FakeClient(route(exclude=[1, 9], order="cheaper"), extraction,
                        barrier=threading.Barrier(2, timeout=5))
    ctx = ParseContext(pending="anc", question_text="주로 어디서 들어?", known=["가격대=$20 미만"],
                       current=["A", "B"])
    result = parse_message(client, SPEC, "지하철에서", ctx)

    assert result.request_type == "shopping"
    assert [(c.attribute, c.value, c.quote) for c in result.constraints] == [("anc", "yes", "지하철")]
    assert result.rejected == ["connection=bluetooth"]
    assert result.notes == ["지하철 출퇴근"] and result.keywords == ["commute"]
    assert result.exclude_ranks == [1] and result.price_order == "cheaper"
    assert {c[0] for c in client.calls} == {RouteOutput, Extraction}
    extract_prompt = next(c[1] for c in client.calls if c[0] is Extraction)
    assert "직전 질문: 주로 어디서 들어?" in extract_prompt and "질문 의도: anc" in extract_prompt


def test_adjustments_are_ignored_without_current_recommendations():
    client = FakeClient(route(exclude=[1], order="cheaper"), Extraction(constraints=[], notes=[], keywords=[]))
    result = parse_message(client, SPEC, "더 싼 걸로")
    assert result.exclude_ranks == [] and result.price_order == "none"


def test_llm_parser_returns_none_on_failure():
    client = FakeClient(route(), Extraction(constraints=[], notes=[], keywords=[]), fail=True)
    assert LLMParser(client)(SPEC, "무선", ParseContext()) is None


def test_extract_input_includes_question_and_memory():
    text = extract_input(SPEC, "카페에서", ExtractContext("anc", "어디서 들어?", ["맥락: 공부"]))
    assert text.splitlines() == ["직전 질문: 어디서 들어?", "질문 의도: anc (노이즈 캔슬링(ANC))",
                                 "지금까지 알게 된 것:", "- 맥락: 공부", "사용자 발화: 카페에서"]


def test_question_input_has_flags_history_and_learned():
    req = QuestionRequest(attribute="anc", message="음 글쎄", retry=True, unclear=True,
                          learned=["가격대: $20 미만"],
                          known=["연결 방식: 무선 (근거: \"무선\")"],
                          history=[{"role": "user", "content": "무선"},
                                   {"role": "assistant", "content": "어디서 써?"}])
    text = question_input(SPEC, req)
    assert text.startswith("알아낼 것: 노이즈 캔슬링(ANC) (가능한 값: 필요, 불필요.")
    assert "상황: 다시 묻기, 방금 한 말에서 알아들은 것 없음" in text
    assert "친구: 무선\n나: 어디서 써?\n방금 알게 된 것: 가격대: $20 미만" in text
    assert text.endswith("친구가 방금 한 말: 음 글쎄")
    assert "직접 묻기" not in text


def test_question_input_marks_direct_attribute():
    """ask_directly 속성(가격대)은 돌려 묻지 않고, 금액을 원화로 말하게 한다."""
    text = question_input(SPEC, QuestionRequest(attribute="price_band"))
    assert "상황: 직접 묻기" in text
    assert "원화로 말한다" in text


@pytest.mark.parametrize("raw, expected", [
    ('  "출근길엔 뭐 하면서 가?"  ', WrittenQuestion("출근길엔 뭐 하면서 가?", "출퇴근 장면")), ("", None)])
def test_question_writer_output(raw, expected):
    client = FakeClient(route(), None, text=raw)
    assert LLMQuestionWriter(client)(SPEC, QuestionRequest(attribute="anc")) == expected


def test_question_writer_returns_none_on_failure():
    client = FakeClient(route(), None, fail=True)
    assert LLMQuestionWriter(client)(SPEC, QuestionRequest(attribute="anc")) is None


def test_persona_prompt_has_no_category_specific_questions():
    """돌려 묻는 질문은 미리 정하지 않는다. 프롬프트에는 방법과 다른 상품 예시만 있다."""
    text = build_instructions(SPEC)
    for attr in SPEC.attributes:
        assert attr.label not in text
