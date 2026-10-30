"""카테고리 정의대로 후보 상품을 고르고 속성을 추출해 SQLite에 저장한다.

입력: data/interim/<id>.jsonl (scripts/filter_category.py 결과)
처리: 후보 필터(candidate_filter) → 제목 중복 제거(리뷰 수 많은 쪽 유지) → 속성 추출
      → 비례 층화 추출(sampling, 정의되어 있으면)
출력: 속성별 분포 리포트(후보 vs 카탈로그), SQLite, data/catalog/<id>.json

    uv run python scripts/build_catalog.py headphones --dry-run    # 리포트만
    uv run python scripts/build_catalog.py headphones
    uv run python scripts/build_catalog.py headphones --no-sample  # 후보 전체 저장

@see README.md#방법-b-원본에서-만들기
@see docs/architecture.md#1-한눈에-보기
"""

import argparse
import json
import re
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.extract import Extracted, extract_attributes  # noqa: E402
from app.catalog.repository import export_catalog, replace_products  # noqa: E402
from app.catalog.sampling import eligible, stratified_sample  # noqa: E402
from app.catalog.spec import CategorySpec, load_category_spec  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

Row = tuple[dict, dict[str, Extracted]]


def is_candidate(spec: CategorySpec, item: dict) -> bool:
    f = spec.candidate_filter
    if f.require_price and not isinstance(item.get("price"), (int, float)):
        return False
    return (item.get("rating_number") or 0) >= f.min_rating_number


def dedupe_by_title(items: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for item in items:
        key = re.sub(r"\W+", " ", (item.get("title") or "").casefold()).strip()
        if key not in best or (item.get("rating_number") or 0) > (best[key].get("rating_number") or 0):
            best[key] = item
    return list(best.values())


def distribution(rows: list[Row], key: str) -> Counter[str]:
    return Counter(attrs[key].value if key in attrs else "unknown" for _, attrs in rows)


def print_report(spec: CategorySpec, population: list[Row], sample: list[Row]) -> None:
    """속성마다 후보(population)와 카탈로그(sample)의 값 비율을 나란히 보여준다."""
    print(f"\n{'':24}{'candidates':>18}{'catalog':>16}")
    print(f"{'':24}{len(population):>18,}{len(sample):>16,}")
    for attr in spec.attributes:
        pop, smp = distribution(population, attr.key), distribution(sample, attr.key)
        print(f"\n[{attr.key}]")
        for value, c in pop.most_common(8 if attr.is_open else None):
            s = smp.get(value, 0)
            print(f"  {value[:20]:20}  {c:7,} ({100 * c / len(population):5.1f}%)"
                  f"  {s:5,} ({100 * s / len(sample):5.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category", help="categories/<id>.yaml의 id")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 리포트만 출력")
    parser.add_argument("--no-sample", action="store_true", help="샘플링 없이 후보 전체 사용")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()

    spec = load_category_spec(args.category)
    with (DATA_DIR / "interim" / f"{spec.id}.jsonl").open(encoding="utf-8") as f:
        items = [json.loads(line) for line in f]
    candidates = [i for i in items if is_candidate(spec, i)]
    unique = dedupe_by_title(candidates)
    rows = [(item, extract_attributes(spec, item)) for item in unique]
    print(f"interim {len(items):,} -> candidates {len(candidates):,} -> deduped {len(unique):,}")

    if spec.sampling and not args.no_sample:
        population = eligible(rows, spec.sampling)
        catalog = stratified_sample(rows, spec.sampling)
        print(f"eligible {len(population):,} -> sampled {len(catalog):,} "
              f"(stratify_by={spec.sampling.stratify_by}, seed={spec.sampling.seed})")
    else:
        population = catalog = rows
    print_report(spec, population, catalog)

    if args.dry_run:
        return
    init_db(args.db)
    with closing(connect(args.db)) as conn:
        count = replace_products(conn, spec, catalog)
        conn.commit()
        exported = export_catalog(conn, spec.id)
    out = DATA_DIR / "catalog" / f"{spec.id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(exported, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nsaved {count:,} products -> {args.db}\nexported -> {out}")


if __name__ == "__main__":
    main()
