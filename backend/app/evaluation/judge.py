"""LLM Judge: score one shopping episode using persona, dialogue and recommendations.

Literature informing the evaluation dimensions (not copied scoring scales):
  [1] Chen, N., Dai, Q., Dong, X., Wu, X.-M., & Dong, Z. (2025).
      "Evaluating Conversational Recommender Systems with Large Language Models:
      A User-Centric Evaluation Framework." arXiv:2501.09493.
      https://arxiv.org/abs/2501.09493
      Separates language expression from recommendation-item evaluation.
      Its linguistic "appropriateness" should NOT be confused with our item fit.
  [2] Liu, Y., et al. (2023). "G-Eval: NLG Evaluation using GPT-4 with
      Better Human Alignment." EMNLP 2023.
      https://aclanthology.org/2023.emnlp-main.153/
      Motivates explicit evaluation criteria for LLM-based judging.
  [3] Jannach, D. (2023). "Evaluating conversational recommender systems:
      A landscape of research." Artificial Intelligence Review.
      https://doi.org/10.1007/s10462-022-10229-x
      Motivates evaluating recommendation outcomes and interaction experience.
  [4] Zheng, L., et al. (2023). "Judging LLM-as-a-Judge with MT-Bench and
      Chatbot Arena." arXiv:2306.05685.
      https://arxiv.org/abs/2306.05685
      Warns about potential LLM-judge biases.

The 1-5 anchors below are PROJECT-DESIGNED rubrics inspired by these works,
NOT scales reproduced verbatim from the referenced studies. Scores measured
under the old and new prompts must not be compared as if they used one rubric.

Keep the JudgeScores schema unchanged for compatibility with the runner/API.
@see docs/evaluation.md
"""

from pydantic import BaseModel, Field

from app.catalog.repository import Product
from app.catalog.spec import CategorySpec
from app.evaluation.scenarios import Scenario
from app.llm import LLMClient
from app.services.responses import change_label


class JudgeScores(BaseModel):
    """Existing result schema: preserved for runner, database and API compatibility."""

    fit: int = Field(description="How well recommended items meet preferences, 1-5")
    naturalness: int = Field(description="How natural chatbot's conversation feels, 1-5")
    efficiency: int = Field(description="Efficiency without unnecessary questions, 1-5")
    comment: str = Field(description="Brief evidence-based explanation of scores")


# Chen et al. [1]: linguistic naturalness and recommendation-item suitability
# are different axes. Liu et al. [2]: make the criteria explicit to the judge.
# The anchors themselves were designed for this project and its 1-5 interface.
INSTRUCTIONS = "\n".join([
    "You are an impartial evaluator of a conversational product recommender.",
    "You receive a simulated shopper's hidden preferences, the conversation,",
    "and recommended products with the available catalog attributes and prices.",
    "Score fit, naturalness and efficiency INDEPENDENTLY, each from 1 to 5.",
    "Use observed evidence and the following project-specific scoring rubric.",
    "",
    "NATURALNESS (chatbot language and conversational flow, NOT recommendation quality):",
    "Assess fluency, coherent transitions, acknowledgment of previous shopper",
    "answers, and whether the chatbot sounds conversational rather than scripted.",
    "5: consistently fluent, contextual, and smooth; no noticeably robotic turns.",
    "4: generally natural; only minor awkward or formulaic moments.",
    "3: understandable, but noticeably scripted, abrupt, or survey-like at times.",
    "2: frequent awkward phrasing or weak conversational continuity.",
    "1: persistently unnatural, confusing, or incoherent chatbot language.",
    "Judge the CHATBOT's language, not the simulated shopper's writing style.",
    "Do not reward verbosity, excessive friendliness, or unnecessary questions.",
    "",
    "FIT / RECOMMENDATION APPROPRIATENESS (product suitability, NOT writing style):",
    "Compare recommended products, especially recommendation #1, against",
    "preferences in the hidden persona and the conversation. Prioritize hard",
    "constraints (e.g. budget, connectivity, form factor) over softer preferences.",
    "Consider all recommended products; weigh #1 most heavily.",
    "5: #1 meets all verifiable key constraints and strongly matches the use case;",
    "   remaining products provide sensible alternatives.",
    "4: key constraints are satisfied, with only minor gaps or uncertainty.",
    "3: partly suitable, with one important mismatch or substantial uncertainty.",
    "2: several important mismatches or weak relevance to shopper needs.",
    "1: recommendations are largely irrelevant, or no products were recommended.",
    "Missing feature information is NOT evidence the feature is absent or present.",
    "Do not invent catalog specifications or assume unlisted product properties.",
    "Prefer newer explicit shopper statements if preferences were corrected.",
    "Do not require the historically purchased item: its rank is scored separately.",
    "",
    "EFFICIENCY (keep the existing evaluation dimension):",
    "5: no unnecessary or repetitive questions; efficient progress to recommendations.",
    "4: mostly efficient, with only minor redundancy.",
    "3: some avoidable questions or delays.",
    "2: significant repetition or low-value questions that slow progress.",
    "1: persistent repetition, wasted turns, or inability to progress.",
    "Do not score efficiency by number of questions alone; useful clarification counts.",
    "",
    "GENERAL RULES:",
    "Score each dimension separately: fluent dialogue can still recommend badly,",
    "and awkward dialogue can still recommend useful products.",
    "Do not favor any system by its identity, response length, or perceived style.",
    "In comment, briefly justify fit and naturalness with specific observable",
    "details from the recommendations or transcript; mention efficiency if useful.",
    "Return only the requested structured fields.",
])


def describe_product(spec: CategorySpec, product: Product) -> str:
    """Include only available catalog evidence; never hallucinate specifications."""
    attrs = ", ".join(
        change_label(spec, k, v, "en")
        for k, v in product.attributes.items()
        if k != "brand"
    )
    price = f"${product.price:.2f}" if product.price is not None else "price not listed"
    return f"{product.title[:100]} | {price} | {attrs}"


def judge_episode(client: LLMClient, spec: CategorySpec, scenario: Scenario,
                  transcript: list[dict[str, str]], recommended: list[Product]) -> JudgeScores:
    """Judge one episode without exposing the historical target product ID.

    The judge sees hidden preferences, the transcript, and catalog-backed
    descriptions of suggested products. Target-item rank is scored separately.

    Research basis: Chen et al. [1], Liu et al. [2], Jannach [3].
    @docid eval.judge
    @see docs/evaluation.md#3-에피소드와-지표
    """
    dialogue = "\n".join(
        f"{'Shopper' if m['role'] == 'user' else 'Chatbot'}: {m['content']}"
        for m in transcript
    )
    recs = "\n".join(
        f"{i}. {describe_product(spec, p)}"
        for i, p in enumerate(recommended, 1)
    ) or "(none)"
    persona = "\n".join(f"- {p}" for p in scenario.persona)
    content = (
        f"Hidden preferences:\n{persona}\n\n"
        f"Conversation:\n{dialogue}\n\nRecommendations:\n{recs}"
    )
    scores = client.parse(
        [{"role": "user", "content": content}],
        JudgeScores,
        instructions=INSTRUCTIONS,
    )
    # Preserve the existing output contract (scores between 1 and 5).
    for name in ("fit", "naturalness", "efficiency"):
        setattr(scores, name, min(max(int(getattr(scores, name)), 1), 5))
    return scores
