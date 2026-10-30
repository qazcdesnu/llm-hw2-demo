from functools import lru_cache

from app.config import settings
from app.llm.base import ChatMessage, LLMClient, LLMError, LLMNotConfiguredError
from app.llm.openai_client import OpenAIClient

__all__ = [
    "ChatMessage", "LLMClient", "LLMError", "LLMNotConfiguredError", "OpenAIClient",
    "get_llm_client",
]


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
