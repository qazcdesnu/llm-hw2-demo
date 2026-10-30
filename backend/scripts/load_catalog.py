"""git에 포함된 카탈로그 JSON(data/catalog/<id>.json)을 SQLite에 넣는다.

원본 데이터(5GB)를 받지 않고 앱을 실행할 때 쓴다. 원본에서 다시 만들려면 build_catalog.py.

    uv run python scripts/load_catalog.py headphones
"""

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.extract import Extracted  # noqa: E402
from app.catalog.repository import replace_products  # noqa: E402
from app.catalog.spec import load_category_spec  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402

CATALOG_DIR = Path(__file__).resolve().parent.parent / "data" / "catalog"


def rows_from_export(data: dict) -> list[tuple[dict, dict[str, Extracted]]]:
    """export_catalog 형식 → replace_products 입력."""
    rows = []
    for p in data["products"]:
        item = {
            "parent_asin": p["id"], "title": p["title"], "store": p.get("store"),
            "price": p.get("price"), "average_rating": p.get("average_rating"),
            "rating_number": p.get("rating_number"),
            "images": [{"variant": "MAIN", "large": p["image_url"]}] if p.get("image_url") else [],
            "features": p.get("features") or [], "description": p.get("description") or [],
            "details": p.get("details") or {},
        }
        attrs = {k: Extracted(a["value"], a["source"], a.get("evidence"))
                 for k, a in (p.get("attributes") or {}).items()}
        rows.append((item, attrs))
    return rows


def load(category: str, db_path: Path) -> int:
    spec = load_category_spec(category)
    data = json.loads((CATALOG_DIR / f"{category}.json").read_text(encoding="utf-8"))
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        count = replace_products(conn, spec, rows_from_export(data))
        conn.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category", help="data/catalog/<id>.json의 id")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()
    print(f"loaded {load(args.category, args.db):,} products -> {args.db}")


if __name__ == "__main__":
    main()
