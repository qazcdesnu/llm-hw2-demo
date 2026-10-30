"""원본 메타 파일에서 한 카테고리의 상품만 골라 data/interim/<id>.jsonl로 저장한다.

어떤 원본 파일의 어떤 카테고리를 쓸지는 categories/<id>.yaml의 source에 정의한다.

    uv run python scripts/filter_category.py headphones

@see README.md#방법-b-원본에서-만들기
@see docs/architecture.md#1-한눈에-보기
@see docs/data-analysis.md#헤드폰-데이터-분석-2026-10-26
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.spec import load_category_spec  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category", help="categories/<id>.yaml의 id")
    args = parser.parse_args()

    spec = load_category_spec(args.category)
    src = DATA_DIR / "raw" / spec.source.raw_file
    dst = DATA_DIR / "interim" / f"{spec.id}.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)

    target = spec.source.category
    total = kept = 0
    leaves: Counter[str] = Counter()

    with src.open(encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            total += 1
            # 대부분의 줄은 json 파싱 없이 문자열 검사로 건너뛴다.
            if target not in line:
                continue
            item = json.loads(line)
            categories = item.get("categories") or []
            if target not in categories:
                continue
            kept += 1
            leaves[" > ".join(categories[categories.index(target) + 1 :]) or "(none)"] += 1
            fout.write(line)

    print(f"scanned {total:,} items, kept {kept:,} -> {dst}")
    for leaf, n in leaves.most_common():
        print(f"{n:8,}  {leaf}")


if __name__ == "__main__":
    main()
