"""평가 시나리오: Amazon-C4의 (질의, 리뷰, 실제 구매 상품)에서 가상 사용자를 만든다.

C4 질의 중 정답 상품(item_id)이 우리 카탈로그에 있고, 아래 조건을 만족하는 것만 쓴다.
- 긍정 리뷰(평점 5): 만족한 구매여야 그 상품이 이 사람의 정답이 된다. C4는 원래 5점 리뷰로만 만들었지만 명시해 둔다.
- 리뷰가 충분히 김(기본 300자 이상): 짧은 리뷰("Dad loves them")에는 상황·선호 정보가 거의 없다. LLM이 질의·리뷰·정답 상품의 속성으로
한국어 페르소나(숨겨진 선호)와 첫 발화를 만든다. 정답은 실제 구매 상품이다.
가상 사용자는 정답 상품의 속성에 맞게 답하므로, 질문에 일관되게 답한다(IG 평가의 표준 방식).
"""

import csv
import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from app.catalog.repository import Product
from app.catalog.spec import CategorySpec
from app.config import BACKEND_DIR
from app.llm import LLMClient
from app.services.responses import change_label

C4_TEST = BACKEND_DIR / "data" / "raw" / "amazon-c4" / "test.csv"
SCENARIO_DIR = BACKEND_DIR / "evaluation" / "scenarios"
REVIEW_CHARS = 1500
MIN_RATING = 5.0
MIN_REVIEW_CHARS = 300  # 후보 126개 중 44개가 남는다 (중앙값 234자)


@dataclass
class Scenario:
    id: str  # c4-<qid>
    category_id: str
    target_id: str  # 실제 구매 상품 (parent_asin)
    query: str  # C4 원문 질의 (영어)
    review: str  # C4 원문 리뷰 (영어, 앞부분)
    persona: list[str]  # 가상 사용자의 선호 (한국어)
    opening: str  # 첫 발화
    brief: str  # 사람이 역할극할 때 보는 한 줄 상황
    target_attributes: dict[str, str] = field(default_factory=dict)  # 정답 상품의 속성 (채점용)


def scenario_path(category_id: str) -> Path:
    return SCENARIO_DIR / f"{category_id}.jsonl"


def load_scenarios(category_id: str) -> list[Scenario]:
    path = scenario_path(category_id)
    if not path.exists():
        return []
    return [Scenario(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]


def save_scenarios(category_id: str, scenarios: list[Scenario]) -> Path:
    path = scenario_path(category_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(asdict(s), ensure_ascii=False) + "\n" for s in scenarios),
                    encoding="utf-8")
    return path


def review_text(row: dict) -> str:
    return re.sub(r"<br\s*/?>", " ", row["ori_review"]).strip()


def c4_candidates(product_ids: set[str], path: Path = C4_TEST, min_rating: float = MIN_RATING,
                  min_review_chars: int = MIN_REVIEW_CHARS) -> list[dict]:
    """정답 상품이 카탈로그에 있고, 긍정 리뷰이며 리뷰가 충분히 긴 C4 질의.

    @docid eval.c4-candidates
    @see docs/evaluation.md#1-시나리오-amazon-c4
    """
    with path.open(encoding="utf-8") as f:
        return [row for row in csv.DictReader(f)
                if row["item_id"] in product_ids
                and float(row["ori_rating"] or 0) >= min_rating
                and len(review_text(row)) >= min_review_chars]


def sample_rows(rows: list[dict], n: int, seed: int = 42) -> list[dict]:
    rows = sorted(rows, key=lambda r: int(r["qid"]))
    return random.Random(seed).sample(rows, min(n, len(rows)))


# ---------------------------------------------------------------- 생성

class ScenarioDraft(BaseModel):
    persona: list[str] = Field(description="The shopper's preferences and situation. 4-7 English sentences")
    opening: str = Field(description="The shopper's first message. One English sentence")
    brief: str = Field(description="One-line summary of this shopper's situation, in English")


def _instructions(spec: CategorySpec) -> str:
    return "\n".join([
        f"You create simulated shopper scenarios for evaluating a {spec.name_for('en')} recommendation chatbot.",
        "You get a real Amazon shopper's request (query), their review, and the attributes of the product they bought.",
        "",
        "## persona (hidden preferences, 4-7 sentences, first person is fine)",
        "- Based on the review, describe why, where, when, and how this person will use it, and what bothers them",
        "  (e.g. commute length, workouts, calls, wearing glasses, past problems). The chatbot asks about everyday",
        "  situations, so the persona should cover the person's daily context, not only features.",
        "- Turn the product attributes into this person's preferences (e.g. Wireless → 'hates dealing with cables').",
        "  Do not write preferences that contradict the attributes.",
        "- Write the budget as a range that contains the actual price, not an upper bound below it",
        "  (e.g. $35 → 'about $30-40, no more than $50').",
        "- Skip attributes marked 'not listed'.",
        "- Do not mention brands, product names, or model names.",
        "",
        "## opening (first message)",
        "- Start vaguely like a real shopper, revealing at most one preference (e.g. 'Looking for earbuds for my commute').",
        "",
        "## brief",
        "- A one-line situation summary for a person who will role-play this shopper.",
    ])


def _input(spec: CategorySpec, row: dict, product: Product) -> str:
    attrs = []
    for attr in spec.attributes:
        if attr.key == "brand":
            continue
        value = product.attributes.get(attr.key)
        attrs.append(change_label(spec, attr.key, value, "en") if value else f"{attr.label_for('en')}: not listed")
    price = f"${product.price:.2f}" if product.price is not None else "price not listed"
    return "\n".join([
        f"query: {row['query']}",
        f"review: {review_text(row)[:REVIEW_CHARS]}",
        f"product bought: {product.title[:120]} ({price})",
        "product attributes:", *[f"- {a}" for a in attrs],
    ])


def generate_scenario(client: LLMClient, spec: CategorySpec, row: dict, product: Product) -> Scenario:
    """@see docs/evaluation.md#1-시나리오-amazon-c4"""
    draft = client.parse([{"role": "user", "content": _input(spec, row, product)}], ScenarioDraft,
                         instructions=_instructions(spec))
    return Scenario(
        id=f"c4-{row['qid']}", category_id=spec.id, target_id=product.id,
        query=row["query"], review=review_text(row)[:REVIEW_CHARS],
        persona=[p.strip() for p in draft.persona if p.strip()],
        opening=draft.opening.strip(), brief=draft.brief.strip(),
        target_attributes={k: v for k, v in product.attributes.items() if k != "brand"},
    )
