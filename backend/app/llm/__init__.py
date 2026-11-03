from dataclasses import dataclass
from functools import lru_cache

from app.config import settings
from app.llm.base import ChatMessage, LLMClient, LLMError, LLMNotConfiguredError
from app.llm.openai_client import OpenAIClient

__all__ = [
    "ChatMessage", "LLMClient", "LLMError", "LLMNotConfiguredError", "OpenAIClient",
    "ModelOption", "chat_client", "chat_models", "get_llm_client",
]


@dataclass(frozen=True)
class ModelOption:
    """화면에서 고를 수 있는 대화 모델."""

    id: str
    input_price: float | None = None  # 1M 토큰당 달러 (Standard)
    output_price: float | None = None


# 화면의 모델 선택지: OpenAI API 가격 페이지(developers.openai.com/api/docs/pricing, 2026-11-02)의
# 텍스트 모델 중, 이 키로 Responses API 구조화 출력 호출이 되고 reasoning effort `low`를 받는 19개.
# 해석·질문 작성 모두 effort low로 고정해 쓰므로(I-5, I-12), low를 받지 않는 모델은 넣지 않는다.
# - low를 받지 않아 뺌: gpt-5.5-pro·5.4-pro·5.2-pro·5-pro(medium 이상), chat-latest(medium만),
#   gpt-4.1·4.1-mini·4o·4o-mini(추론 모델 아님)
# - 404라 뺌(폐기 또는 접근 불가): o1, o1-pro, o3-mini, o4-mini, gpt-4.1-nano, gpt-4o-2024-05-13,
#   gpt-4-turbo-2024-04-09, gpt-4-0613, gpt-3.5-turbo(-0125)
CHAT_MODELS = (
    ModelOption("gpt-6-astra", 10.00, 50.00),
    ModelOption("gpt-6.1-sol", 2.00, 10.00),
    ModelOption("gpt-6.1-terra", 1.00, 5.00),
    ModelOption("gpt-6-sol", 2.00, 10.00),
    ModelOption("gpt-6-luna", 0.10, 0.50),
    ModelOption("gpt-5.6-sol", 4.00, 20.00),
    ModelOption("gpt-5.6-terra", 2.00, 12.00),
    ModelOption("gpt-5.6-luna", 0.20, 1.20),
    ModelOption("gpt-5.5", 5.00, 30.00),
    ModelOption("gpt-5.4", 2.50, 15.00),
    ModelOption("gpt-5.4-mini", 0.75, 4.50),
    ModelOption("gpt-5.4-nano", 0.20, 1.25),
    ModelOption("gpt-5.2", 1.75, 14.00),
    ModelOption("gpt-5.1", 1.25, 10.00),
    ModelOption("gpt-5", 1.25, 10.00),
    ModelOption("gpt-5-mini", 0.25, 2.00),
    ModelOption("gpt-5-nano", 0.05, 0.40),
    ModelOption("o3-pro", 20.00, 80.00),
    ModelOption("o3", 2.00, 8.00),
)


@lru_cache
def get_llm_client(model: str | None = None, reasoning_effort: str | None = None) -> LLMClient:
    """설정에 맞는 LLM 클라이언트. 키가 없으면 호출 시점에 LLMNotConfiguredError.

    model·reasoning_effort를 주면 설정 대신 그 값을 쓴다 (질문 작성처럼 역할별로 다른 설정을 쓸 때).
    reasoning_effort에 빈 문자열을 주면 보내지 않는다(비추론 모델).

    @see backend/README.md#구조
    @see docs/architecture.md#1-한눈에-보기
    """
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        raise LLMNotConfiguredError("backend/.env에 OPENAI_API_KEY를 설정하세요.")
    return OpenAIClient(
        api_key=settings.openai_api_key.get_secret_value(),
        model=model or settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort if reasoning_effort is None else reasoning_effort,
        timeout=settings.openai_timeout,
    )


def chat_models() -> list[ModelOption]:
    """화면에 보여 줄 모델 목록. 설정의 기본 모델이 맨 앞이다."""
    default = settings.openai_model
    rest = [m for m in CHAT_MODELS if m.id != default]
    first = next((m for m in CHAT_MODELS if m.id == default), ModelOption(default))
    return [first, *rest]


def chat_client(model: str | None, role: str) -> LLMClient:
    """대화에 쓸 클라이언트. model이 없거나 기본 모델이면 설정 그대로(질문 작성 모델 설정 포함),
    다른 모델을 고르면 해석(role="parse")과 질문 작성(role="question") 모두 그 모델을 쓴다.
    reasoning effort는 기본 모델과 같은 설정값을 보낸다(선택지는 모두 low를 받는 모델).

    @see docs/llm-parser.md#3-평가
    """
    question = role == "question"
    if model is None or model == settings.openai_model:
        if question:
            return get_llm_client(settings.openai_question_model,
                                  settings.openai_question_reasoning_effort or "")
        return get_llm_client()
    option = next((m for m in chat_models() if m.id == model), None)
    if option is None:
        raise ValueError(f"unknown model: {model}")
    effort = settings.openai_question_reasoning_effort if question else settings.openai_reasoning_effort
    return get_llm_client(model, effort or "")
