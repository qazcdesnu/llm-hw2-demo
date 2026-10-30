import logging
from typing import Any

import openai

from app.llm.base import ChatMessage, LLMError, T

logger = logging.getLogger(__name__)


class OpenAIClient:
    """OpenAI Responses API 클라이언트.

    @see docs/architecture.md#1-한눈에-보기
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        reasoning_effort: str | None = None,
        timeout: float = 30.0,
        client: Any | None = None,
    ) -> None:
        # client는 테스트에서 가짜 SDK 객체를 주입할 때 쓴다.
        self._client = client or openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model
        self.reasoning_effort = reasoning_effort or None

    def _request_kwargs(self, messages: list[ChatMessage], instructions: str | None) -> dict:
        kwargs: dict = {"model": self.model, "input": messages}
        if instructions is not None:
            kwargs["instructions"] = instructions
        if self.reasoning_effort is not None:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        return kwargs

    def _log_usage(self, response: Any) -> None:
        # $20 예산 관리를 위해 호출마다 토큰 사용량을 남긴다.
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "openai model=%s input_tokens=%s output_tokens=%s",
                self.model, usage.input_tokens, usage.output_tokens,
            )

    def complete(self, messages: list[ChatMessage], *, instructions: str | None = None) -> str:
        try:
            response = self._client.responses.create(**self._request_kwargs(messages, instructions))
        except openai.OpenAIError as e:
            raise LLMError(f"OpenAI request failed: {e}") from e
        self._log_usage(response)
        return response.output_text

    def parse(
        self, messages: list[ChatMessage], schema: type[T], *, instructions: str | None = None
    ) -> T:
        try:
            response = self._client.responses.parse(
                **self._request_kwargs(messages, instructions), text_format=schema
            )
        except openai.OpenAIError as e:
            raise LLMError(f"OpenAI request failed: {e}") from e
        self._log_usage(response)
        if response.output_parsed is None:
            raise LLMError("OpenAI returned no parsed output (refusal or incomplete response)")
        return response.output_parsed
