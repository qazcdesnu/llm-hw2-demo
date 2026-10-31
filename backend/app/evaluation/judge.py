"""LLM 심판: 한 에피소드(대화 + 추천)를 페르소나 기준으로 채점한다.

정답 상품 적중(Hit@k)은 코드가 결정적으로 계산하고, 심판은 정답 하나로 잴 수 없는 것을 본다.
4,628개 중 정확히 그 상품을 맞히기는 어려워서, "요구에 맞는 상품을 추천했는가"를 함께 봐야 하기 때문이다.
"""

from pydantic import BaseModel, Field

from app.catalog.repository import Product
from app.catalog.spec import CategorySpec
from app.evaluation.scenarios import Scenario
from app.llm import LLMClient
from app.services.responses import change_label


class JudgeScores(BaseModel):
    fit: int = Field(description="How well the recommended products meet the shopper's hidden preferences, 1-5")
    naturalness: int = Field(description="How natural and friendly the chatbot's conversation felt, 1-5")
    efficiency: int = Field(description="Asked only what was needed, without unnecessary or repeated questions, 1-5")
    comment: str = Field(description="One or two sentences explaining the scores")


INSTRUCTIONS = "\n".join([
    "You evaluate a product recommendation chatbot. You get a simulated shopper's hidden preferences,",
    "the conversation, and the recommended products. Score each item as an integer 1-5 (5 is best).",
    "- fit: how well the recommendations (especially #1) meet the hidden preferences. Do not penalize what the",
    "  product info does not list.",
    "- naturalness: how natural the chatbot's tone and flow are. Low if it feels like a stiff survey.",
    "- efficiency: low if it repeats questions or asks useless ones. High if few questions still led to good picks.",
    "Judge by content, not by response length or which system it seems to be.",
])


def describe_product(spec: CategorySpec, product: Product) -> str:
    attrs = ", ".join(change_label(spec, k, v, "en") for k, v in product.attributes.items() if k != "brand")
    price = f"${product.price:.2f}" if product.price is not None else "price not listed"
    return f"{product.title[:100]} | {price} | {attrs}"


def judge_episode(client: LLMClient, spec: CategorySpec, scenario: Scenario,
                  transcript: list[dict[str, str]], recommended: list[Product]) -> JudgeScores:
    """@docid eval.judge
    @see docs/evaluation.md#3-에피소드와-지표
    """
    dialogue = "\n".join(f"{'Shopper' if m['role'] == 'user' else 'Chatbot'}: {m['content']}" for m in transcript)
    recs = "\n".join(f"{i}. {describe_product(spec, p)}" for i, p in enumerate(recommended, 1)) or "(none)"
    persona = "\n".join(f"- {p}" for p in scenario.persona)
    content = f"Hidden preferences:\n{persona}\n\nConversation:\n{dialogue}\n\nRecommendations:\n{recs}"
    scores = client.parse([{"role": "user", "content": content}], JudgeScores, instructions=INSTRUCTIONS)
    for name in ("fit", "naturalness", "efficiency"):
        setattr(scores, name, min(max(int(getattr(scores, name)), 1), 5))
    return scores
