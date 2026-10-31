from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# {"role": "user" | "assistant" | "system", "content": str}
ChatMessage = dict[str, str]


class LLMError(RuntimeError):
    """LLM 호출이 실패했을 때. provider별 예외를 이 타입으로 감싼다."""


class LLMNotConfiguredError(LLMError):
    """API 키 등 설정이 없어 클라이언트를 만들 수 없을 때."""


class LLMClient(Protocol):
    """LLM provider 공통 인터페이스. 새 provider는 이 세 메서드를 구현한다.

    @docid llm.client
    @see backend/README.md#구조
    @see docs/architecture.md#1-한눈에-보기
    """

    def complete(self, messages: list[ChatMessage], *, instructions: str | None = None) -> str:
        """자유 텍스트 응답."""
        ...

    def parse(
        self, messages: list[ChatMessage], schema: type[T], *, instructions: str | None = None
    ) -> T:
        """schema(Pydantic 모델)에 맞춘 구조화 응답."""
        ...

    def embed(self, texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
        """글마다 임베딩 벡터."""
        ...
