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
            *self.history[:-1], {"role": "user", "content": f"{incoming}\n\n[Instruction] {note}"}]
        out = self.client.complete(messages, instructions=self.instructions).strip()
        self.history.append({"role": "assistant", "content": out})
        return out


def simulated_user(client: LLMClient, scenario: Scenario, category_name: str) -> ChatAgent:
    """시나리오의 페르소나를 숨겨진 선호로 가진 가상 사용자.

    @docid eval.simulated-user
    @see docs/briefing.md#5-평가-방법-evaluationmd
    @see docs/evaluation.md#3-에피소드와-지표
    """
    persona = "\n".join(f"- {p}" for p in scenario.persona)
    return ChatAgent(client, "\n".join([
        f"You are an online shopper looking to buy {category_name}. You are chatting with a recommendation chatbot.",
        "Your real situation and preferences are below. Do not reveal them all at once before you are asked.",
        persona,
        "",
        "Rules:",
        "- Write in casual, natural English, like a real person texting. Answer only what was asked, in 1-2 sentences.",
        "- If your preferences above answer the question, answer accordingly. If the chatbot asks indirectly",
        "  about your daily life, answer honestly from your situation.",
        "- If your preferences do not cover it, answer the way this person would most plausibly answer given their",
        "  situation. Say you're not sure or don't mind only when you really can't tell. Do not invent strong",
        "  requirements that are not in your preferences.",
        "- Do not bring up product names or brands first.",
        "- You are the customer. Do not ask questions or recommend anything. No quotes or labels, just your message.",
    ]))
