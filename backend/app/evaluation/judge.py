"""LLM 심판: 한 에피소드(대화 + 추천)를 페르소나 기준으로 채점한다.

정답 상품 적중(Hit@k)은 코드가 결정적으로 계산하고, 심판은 정답 하나로 잴 수 없는 것을 본다.
4,628개 중 정확히 그 상품을 맞히기는 어려워서, "요구에 맞는 상품을 추천했는가"를 함께 봐야 하기 때문이다.
"""

from pydantic import BaseModel, Field

from app.catalog.repository import Product
from app.catalog.spec import CategorySpec
from app.evaluation.scenarios import Scenario
from app.llm import LLMClient
from app.services.memory import label_of


class JudgeScores(BaseModel):
    fit: int = Field(description="추천 상품들이 사용자의 숨겨진 선호를 얼마나 충족하는가 1~5")
    naturalness: int = Field(description="챗봇의 대화가 친구처럼 자연스럽고 편했는가 1~5")
    efficiency: int = Field(description="불필요하거나 반복된 질문 없이 필요한 것만 물었는가 1~5")
    comment: str = Field(description="채점 근거 한두 문장 (한국어)")


INSTRUCTIONS = "\n".join([
    "너는 상품 추천 챗봇 평가자다. 가상 사용자의 숨겨진 선호, 대화 기록, 추천된 상품이 주어진다.",
    "각 항목을 1~5 정수로 채점한다. 5가 가장 좋다.",
    "- fit: 추천 상품(특히 1위)이 숨겨진 선호를 충족하는 정도. 상품 정보에 없는 것은 감점하지 않는다.",
    "- naturalness: 챗봇 말투와 흐름이 자연스러운 정도. 딱딱한 설문식이면 낮게.",
    "- efficiency: 같은 것을 다시 묻거나 쓸모없는 질문이 많으면 낮게. 질문이 적어도 추천이 맞으면 높게.",
    "대화에 나온 챗봇 응답의 길이나 시스템 종류로 판단하지 말고 내용으로 판단한다.",
])


def describe_product(spec: CategorySpec, product: Product) -> str:
    attrs = ", ".join(label_of(spec, k, v) for k, v in product.attributes.items() if k != "brand")
    price = f"${product.price:.2f}" if product.price is not None else "가격 정보 없음"
    return f"{product.title[:100]} | {price} | {attrs}"


def judge_episode(client: LLMClient, spec: CategorySpec, scenario: Scenario,
                  transcript: list[dict[str, str]], recommended: list[Product]) -> JudgeScores:
    """@docid eval.judge
    @see docs/evaluation.md#3-에피소드와-지표
    """
    dialogue = "\n".join(f"{'사용자' if m['role'] == 'user' else '챗봇'}: {m['content']}" for m in transcript)
    recs = "\n".join(f"{i}. {describe_product(spec, p)}" for i, p in enumerate(recommended, 1)) or "(추천 없음)"
    persona = "\n".join(f"- {p}" for p in scenario.persona)
    content = f"숨겨진 선호:\n{persona}\n\n대화:\n{dialogue}\n\n추천:\n{recs}"
    scores = client.parse([{"role": "user", "content": content}], JudgeScores, instructions=INSTRUCTIONS)
    for name in ("fit", "naturalness", "efficiency"):
        setattr(scores, name, min(max(int(getattr(scores, name)), 1), 5))
    return scores
