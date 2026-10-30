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
from app.services.memory import label_of

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
    persona: list[str] = Field(description="가상 사용자의 선호와 상황. 한국어 문장 3~6개")
    opening: str = Field(description="가상 사용자의 첫 마디. 한국어 한 문장")
    brief: str = Field(description="이 사람의 상황 한 줄 요약. 한국어")


def _instructions(spec: CategorySpec) -> str:
    return "\n".join([
        f"너는 '{spec.name}' 추천 챗봇을 평가할 가상 사용자 시나리오를 만든다.",
        "실제 구매자의 요청(query)과 리뷰(review), 실제로 산 상품의 속성이 주어진다.",
        "",
        "## persona (숨겨진 선호, 3~6개 문장)",
        "- 이 사람이 왜, 어디서, 어떻게 쓰려는지 리뷰에 근거해 쓴다.",
        "- 주어진 상품 속성은 이 사람의 선호로 바꿔 쓴다(예: 무선 → '선 없는 게 좋다'). 속성과 어긋나는 선호는 쓰지 않는다.",
        "- 가격은 실제 가격을 넘지 않는 상한이 아니라, 실제 가격이 들어가는 예산으로 쓴다(예: $35 → '5만 원 안쪽').",
        "- '상품 정보 없음'인 속성은 쓰지 않는다.",
        "- 브랜드·상품명·모델명은 쓰지 않는다.",
        "",
        "## opening (첫 마디)",
        "- 실제 사용자처럼 막연하게 시작한다. 선호를 많아야 하나만 드러낸다(예: '출퇴근할 때 쓸 이어폰 찾고 있어').",
        "",
        "## brief",
        "- 사람이 이 사용자를 연기할 때 볼 상황 요약 한 줄.",
    ])


def _input(spec: CategorySpec, row: dict, product: Product) -> str:
    attrs = []
    for attr in spec.attributes:
        if attr.key == "brand":
            continue
        value = product.attributes.get(attr.key)
        attrs.append(label_of(spec, attr.key, value) if value else f"{attr.label}: 상품 정보 없음")
    price = f"${product.price:.2f}" if product.price is not None else "가격 정보 없음"
    return "\n".join([
        f"query: {row['query']}",
        f"review: {review_text(row)[:REVIEW_CHARS]}",
        f"실제 산 상품: {product.title[:120]} ({price})",
        "상품 속성:", *[f"- {a}" for a in attrs],
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
