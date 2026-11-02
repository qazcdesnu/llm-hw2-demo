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
    reasoning: bool  # reasoning effort를 받는 추론 모델인가 (아니면 보내지 않는다)


# 화면의 모델 선택지. 기본값은 설정의 OPENAI_MODEL (목록에 없으면 맨 앞에 더한다).
# gpt-4.1-nano는 OpenAI에서 폐기되어(404) 넣지 않았다 (I-5).
CHAT_MODELS = (
    ModelOption("gpt-5-mini", True),
    ModelOption("gpt-5-nano", True),
    ModelOption("gpt-5", True),
    ModelOption("gpt-4.1-mini", False),
    ModelOption("gpt-4o-mini", False),
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
    first = next((m for m in CHAT_MODELS if m.id == default),
                 ModelOption(default, bool(settings.openai_reasoning_effort)))
    return [first, *rest]


def chat_client(model: str | None, role: str) -> LLMClient:
    """대화에 쓸 클라이언트. model이 없거나 기본 모델이면 설정 그대로(질문 작성 모델 설정 포함),
    다른 모델을 고르면 해석(role="parse")과 질문 작성(role="question") 모두 그 모델을 쓴다.
    추론 모델이 아니면 reasoning effort를 보내지 않는다.

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
    return get_llm_client(model, (effort or "") if option.reasoning else "")
