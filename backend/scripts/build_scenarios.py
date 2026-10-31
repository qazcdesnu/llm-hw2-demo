"""Amazon-C4에서 평가 시나리오를 만든다 (실제 LLM 호출).

    uv run python scripts/download_amazon_c4.py          # 먼저 C4를 받는다
    uv run python scripts/build_scenarios.py headphones --limit 10

정답 상품이 카탈로그(SQLite)에 있고 긍정(평점 ≥ --min-rating)·충분히 긴(≥ --min-review-chars) 리뷰의
C4 질의 중 limit개를 seed로 뽑아, LLM으로 페르소나·첫 마디를 만든다.
결과: evaluation/scenarios/<id>.jsonl (git 포함)

@see docs/briefing.md#8-남은-문제-다음-단계-일-나누기
@see docs/evaluation.md#1-시나리오-amazon-c4
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.repository import load_category, load_products  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect  # noqa: E402
from app.evaluation.scenarios import (  # noqa: E402
    C4_TEST, MIN_RATING, MIN_REVIEW_CHARS, c4_candidates, generate_scenario, sample_rows, save_scenarios,
)
from app.llm import get_llm_client  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-rating", type=float, default=MIN_RATING)
    parser.add_argument("--min-review-chars", type=int, default=MIN_REVIEW_CHARS)
    args = parser.parse_args()

    if not C4_TEST.exists():
        sys.exit(f"{C4_TEST} 없음. scripts/download_amazon_c4.py를 먼저 실행한다")
    with closing(connect(settings.db_path)) as conn:
        spec = load_category(conn, args.category)
        products = {p.id: p for p in load_products(conn, args.category)}
    if spec is None:
        sys.exit("카탈로그 없음. scripts/load_catalog.py를 먼저 실행한다")

    in_catalog = c4_candidates(set(products), min_rating=0, min_review_chars=0)
    rows = c4_candidates(set(products), min_rating=args.min_rating, min_review_chars=args.min_review_chars)
    picked = sample_rows(rows, args.limit, args.seed)
    print(f"C4 질의 중 정답이 카탈로그에 있는 것 {len(in_catalog)}개 → 평점 ≥ {args.min_rating}, "
          f"리뷰 ≥ {args.min_review_chars}자 {len(rows)}개 → {len(picked)}개 사용")
    client = get_llm_client()
    with ThreadPoolExecutor(max_workers=8) as pool:
        scenarios = list(pool.map(lambda r: generate_scenario(client, spec, r, products[r["item_id"]]), picked))
    path = save_scenarios(args.category, scenarios)
    for s in scenarios:
        print(f"  {s.id}: {s.brief}  /  첫 마디: {s.opening}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
