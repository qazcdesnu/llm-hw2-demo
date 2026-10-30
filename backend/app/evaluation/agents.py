"""대화 기록을 들고 다니는 LLM 에이전트: 가상 사용자, LLM 비교군의 추천자."""

from app.evaluation.scenarios import Scenario
from app.llm import LLMClient


class ChatAgent:
    """시스템 프롬프트 + 지금까지의 대화로 다음 말을 만든다."""

    def __init__(self, client: LLMClient, instructions: str):
        self.client = client
        self.instructions = instructions
        self.history: list[dict[str, str]] = []

    def reply(self, incoming: str, *, note: str | None = None) -> str:
        """incoming에 답한다. note는 이번 턴에만 붙이는 지시(대화 기록에는 남기지 않음)."""
        self.history.append({"role": "user", "content": incoming})
        messages = self.history if note is None else [
            *self.history[:-1], {"role": "user", "content": f"{incoming}\n\n[지시] {note}"}]
        out = self.client.complete(messages, instructions=self.instructions).strip()
        self.history.append({"role": "assistant", "content": out})
        return out


def simulated_user(client: LLMClient, scenario: Scenario, category_name: str) -> ChatAgent:
    """시나리오의 페르소나를 숨겨진 선호로 가진 가상 사용자.

    @docid eval.simulated-user
    @see docs/evaluation.md#3-에피소드와-지표
    """
    persona = "\n".join(f"- {p}" for p in scenario.persona)
    return ChatAgent(client, "\n".join([
        f"너는 {category_name}을(를) 사려는 쇼핑몰 고객이다. 추천 챗봇과 메신저로 대화한다.",
        "너의 실제 상황과 선호는 아래와 같다. 이 내용은 상대가 묻기 전에 한꺼번에 말하지 않는다.",
        persona,
        "",
        "규칙:",
        "- 상대가 물은 것에만 짧게 1~2문장으로, 편한 말투로 답한다.",
        "- 위 선호에서 답을 알 수 있으면 그대로 답한다. 돌려 물어도 네 상황에 비춰 솔직하게 답한다.",
        "- 위 선호에 없는 것을 물으면 '잘 모르겠어' 또는 '상관없어'라고 답한다.",
        "- 상품명·브랜드는 먼저 말하지 않는다.",
        "- 너는 고객이다. 질문하거나 추천하지 않는다. 따옴표·머리말 없이 할 말만 쓴다.",
    ]))
