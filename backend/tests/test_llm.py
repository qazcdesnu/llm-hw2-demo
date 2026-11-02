from types import SimpleNamespace

import openai
import pytest
from pydantic import BaseModel

from app.llm import LLMError, LLMNotConfiguredError, OpenAIClient, chat_client, chat_models, get_llm_client


class Pref(BaseModel):
    connection: str | None


class FakeResponses:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def _respond(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.output

    create = _respond
    parse = _respond


def make_client(responses: FakeResponses) -> OpenAIClient:
    return OpenAIClient("key", "test-model", client=SimpleNamespace(responses=responses))


USAGE = SimpleNamespace(input_tokens=10, output_tokens=5)


def test_complete_returns_text_and_passes_instructions():
    fake = FakeResponses(SimpleNamespace(output_text="안녕하세요", usage=USAGE))
    client = make_client(fake)

    assert client.complete([{"role": "user", "content": "hi"}], instructions="be nice") == "안녕하세요"
    assert fake.calls[0] == {
        "model": "test-model",
        "input": [{"role": "user", "content": "hi"}],
        "instructions": "be nice",
    }


def test_complete_omits_instructions_when_none():
    fake = FakeResponses(SimpleNamespace(output_text="ok", usage=None))
    make_client(fake).complete([{"role": "user", "content": "hi"}])
    assert "instructions" not in fake.calls[0]


@pytest.mark.parametrize("effort, expected", [("minimal", {"effort": "minimal"}), ("", None), (None, None)])
def test_reasoning_effort(effort, expected):
    fake = FakeResponses(SimpleNamespace(output_text="ok", usage=None))
    client = OpenAIClient(
        "key", "test-model", reasoning_effort=effort, client=SimpleNamespace(responses=fake)
    )
    client.complete([{"role": "user", "content": "hi"}])
    assert fake.calls[0].get("reasoning") == expected


def test_parse_returns_model():
    fake = FakeResponses(SimpleNamespace(output_parsed=Pref(connection="wireless"), usage=USAGE))
    result = make_client(fake).parse([{"role": "user", "content": "무선"}], Pref)
    assert result == Pref(connection="wireless")
    assert fake.calls[0]["text_format"] is Pref


def test_parse_without_output_raises():
    fake = FakeResponses(SimpleNamespace(output_parsed=None, usage=USAGE))
    with pytest.raises(LLMError):
        make_client(fake).parse([{"role": "user", "content": "x"}], Pref)


def test_sdk_error_is_wrapped():
    fake = FakeResponses(error=openai.APIConnectionError(request=None))
    with pytest.raises(LLMError):
        make_client(fake).complete([{"role": "user", "content": "x"}])


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr("app.llm.settings.openai_api_key", None)
    get_llm_client.cache_clear()
    with pytest.raises(LLMNotConfiguredError):
        get_llm_client()
    get_llm_client.cache_clear()


def test_chat_client_uses_chosen_model_and_skips_effort_for_non_reasoning(monkeypatch):
    """입력창에서 고른 모델: 추론 모델이면 설정의 effort, 아니면 effort를 보내지 않는다."""
    from pydantic import SecretStr

    monkeypatch.setattr("app.llm.settings.openai_api_key", SecretStr("key"))
    monkeypatch.setattr("app.llm.settings.openai_model", "gpt-5-mini")
    monkeypatch.setattr("app.llm.settings.openai_reasoning_effort", "low")
    get_llm_client.cache_clear()
    assert [m.id for m in chat_models()][0] == "gpt-5-mini"
    reasoning = chat_client("gpt-5-nano", "parse")
    assert (reasoning.model, reasoning.reasoning_effort) == ("gpt-5-nano", "low")
    plain = chat_client("gpt-4o-mini", "question")
    assert (plain.model, plain.reasoning_effort) == ("gpt-4o-mini", None)
    assert chat_client(None, "parse").model == "gpt-5-mini"
    with pytest.raises(ValueError):
        chat_client("no-such-model", "parse")
    get_llm_client.cache_clear()
