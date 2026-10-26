"""카탈로그에서 수동 검증용 상품을 뽑아 CSV로 내보낸다.

각 속성마다 추출 값과 근거, 그리고 검증자가 채울 빈 칸(<속성>_check)을 둔다.
check 칸에는 맞으면 비워 두고, 틀리면 올바른 값(모르면 unknown)을 적는다.

    uv run python scripts/export_validation.py headphones --n 40
"""

import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.spec import load_category_spec  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BACKEND_DIR.parent / "docs" / "validation"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category")
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    spec = load_category_spec(args.category)
    catalog = json.loads((BACKEND_DIR / "data" / "catalog" / f"{spec.id}.json").read_text(encoding="utf-8"))
    products = sorted(catalog["products"], key=lambda p: p["id"])
    picked = random.Random(args.seed).sample(products, min(args.n, len(products)))

    keys = [a.key for a in spec.attributes]
    header = ["id", "url", "title", "price"]
    for key in keys:
        header += [key, f"{key}_evidence", f"{key}_check"]
    header.append("note")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = DOCS_DIR / f"{spec.id}_validation.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀에서 한글 깨짐 방지
        writer = csv.writer(f)
        writer.writerow(header)
        for p in picked:
            row = [p["id"], f"https://www.amazon.com/dp/{p['id']}", p["title"], p["price"]]
            for key in keys:
                attr = p["attributes"].get(key)
                row += [attr["value"] if attr else "unknown", (attr or {}).get("evidence") or "", ""]
            row.append("")
            writer.writerow(row)
    print(f"{len(picked)} products -> {out}")


if __name__ == "__main__":
    main()
