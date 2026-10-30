from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SessionCreated(BaseModel):
    session_id: str


class Option(BaseModel):
    value: str
    label: str


class Question(BaseModel):
    """엔진이 지금 묻고 있는 질문. 프론트엔드는 options를 버튼으로 보여준다."""

    attribute: str
    label: str
    text: str
    options: list[Option]  # 마지막은 항상 "상관없음"(dont_care)


class SessionDetail(BaseModel):
    session_id: str
    messages: list[Message]
    question: Question | None = None
    finished: bool = False


class Answer(BaseModel):
    """버튼으로 고른 답. 있으면 message 해석 없이 그대로 쓴다."""

    attribute: str
    value: str


class ChatRequest(BaseModel):
    # 대화 기록에 남는 사용자 발화. 버튼을 누른 경우 보기의 label.
    message: str = Field(min_length=1, max_length=2000)
    answer: Answer | None = None


class Recommendation(BaseModel):
    product_id: str
    title: str
    probability: float
    price: float | None = None
    image_url: str | None = None
    reasons: list[str] = []


class QuestionScore(BaseModel):
    attribute: str
    label: str
    expected_ig: float


class DebugInfo(BaseModel):
    """프론트엔드 Developer Panel에 표시할 엔진 내부 상태.

    @see docs/architecture.md#3-대화-한-턴의-흐름
    """

    turn: int
    selected_attribute: str | None = None
    expected_ig: float | None = None
    entropy: float | None = None
    stop_reason: str | None = None
    question_scores: list[QuestionScore] = []
    top_candidates: list[Recommendation] = []
    preferences: dict = {}
    # LLM 파서의 요청 분류 (shopping / other_product / off_topic / unsafe). 파서를 안 쓴 턴은 None
    request_type: str | None = None
    # 대화 메모리: facts(속성 값과 근거 발화), notes(속성 밖의 사용 맥락)
    memory: dict = {}
    # 질문 문장의 출처: llm(질문 작성기) / template(카테고리 정의의 고정 질문). 질문이 없으면 None
    question_source: str | None = None
    # 질문 작성기가 고른 접근(어떤 생활 장면을 물어 무엇을 알아내려는지)
    question_approach: str | None = None


class ChatResponse(BaseModel):
    reply: str
    question: Question | None = None
    recommendations: list[Recommendation]
    debug: DebugInfo
    finished: bool = False
