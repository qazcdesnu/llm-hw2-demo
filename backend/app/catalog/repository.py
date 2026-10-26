"""카테고리 정의와 상품 카탈로그의 SQLite 저장·조회."""

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.catalog.extract import Extracted
from app.catalog.spec import CategorySpec


@dataclass
class Product:
    id: str
    category_id: str
    title: str
    store: str | None
    price: float | None
    average_rating: float | None
    rating_number: int | None
    image_url: str | None
    # 속성 key -> 값. 없는 key는 unknown.
    attributes: dict[str, str] = field(default_factory=dict)


def _main_image(item: dict) -> str | None:
    images = item.get("images") or []
    main = next((img for img in images if img.get("variant") == "MAIN"), images[0] if images else None)
    return main and (main.get("large") or main.get("hi_res") or main.get("thumb"))


def save_category(conn: sqlite3.Connection, spec: CategorySpec) -> None:
    conn.execute(
        """
        INSERT INTO categories (id, name, spec) VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name, spec = excluded.spec, updated_at = datetime('now')
        """,
        (spec.id, spec.name, spec.model_dump_json()),
    )


def replace_products(
    conn: sqlite3.Connection, spec: CategorySpec, rows: Iterable[tuple[dict, dict[str, Extracted]]]
) -> int:
    """카테고리의 상품을 모두 지우고 rows(원본 상품, 추출 속성)로 바꾼다."""
    save_category(conn, spec)
    conn.execute("DELETE FROM products WHERE category_id = ?", (spec.id,))
    count = 0
    for item, attributes in rows:
        product_id = item["parent_asin"]
        conn.execute(
            """
            INSERT INTO products (id, category_id, title, store, price, average_rating,
                                  rating_number, image_url, features, description, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id, spec.id, item.get("title") or "", item.get("store"),
                item.get("price"), item.get("average_rating"), item.get("rating_number"),
                _main_image(item),
                json.dumps(item.get("features") or [], ensure_ascii=False),
                json.dumps(item.get("description") or [], ensure_ascii=False),
                json.dumps(item.get("details") or {}, ensure_ascii=False),
            ),
        )
        conn.executemany(
            "INSERT INTO product_attributes (product_id, attribute, value, source, evidence)"
            " VALUES (?, ?, ?, ?, ?)",
            [(product_id, key, e.value, e.source, e.evidence) for key, e in attributes.items()],
        )
        count += 1
    return count


def export_catalog(conn: sqlite3.Connection, category_id: str) -> dict:
    """카탈로그를 git에 올릴 수 있는 JSON 구조로 내보낸다 (원본 없이 DB 재구성용)."""
    products = conn.execute(
        """
        SELECT id, title, store, price, average_rating, rating_number, image_url,
               features, description, details
        FROM products WHERE category_id = ? ORDER BY id
        """,
        (category_id,),
    ).fetchall()
    attrs: dict[str, dict] = {}
    for product_id, key, value, source, evidence in conn.execute(
        """
        SELECT pa.product_id, pa.attribute, pa.value, pa.source, pa.evidence
        FROM product_attributes pa JOIN products p ON p.id = pa.product_id
        WHERE p.category_id = ? ORDER BY pa.product_id, pa.attribute
        """,
        (category_id,),
    ):
        attrs.setdefault(product_id, {})[key] = {"value": value, "source": source, "evidence": evidence}
    return {
        "category": category_id,
        "products": [
            {
                "id": p[0], "title": p[1], "store": p[2], "price": p[3], "average_rating": p[4],
                "rating_number": p[5], "image_url": p[6], "features": json.loads(p[7]),
                "description": json.loads(p[8]), "details": json.loads(p[9]),
                "attributes": attrs.get(p[0], {}),
            }
            for p in products
        ],
    }


def load_category(conn: sqlite3.Connection, category_id: str) -> CategorySpec | None:
    row = conn.execute("SELECT spec FROM categories WHERE id = ?", (category_id,)).fetchone()
    return CategorySpec.model_validate_json(row[0]) if row else None


def list_categories(conn: sqlite3.Connection) -> list[CategorySpec]:
    rows = conn.execute("SELECT spec FROM categories ORDER BY id").fetchall()
    return [CategorySpec.model_validate_json(r[0]) for r in rows]


def load_products(conn: sqlite3.Connection, category_id: str) -> list[Product]:
    rows = conn.execute(
        """
        SELECT id, category_id, title, store, price, average_rating, rating_number, image_url
        FROM products WHERE category_id = ? ORDER BY id
        """,
        (category_id,),
    ).fetchall()
    products = {r[0]: Product(*r) for r in rows}
    for product_id, key, value in conn.execute(
        """
        SELECT pa.product_id, pa.attribute, pa.value
        FROM product_attributes pa JOIN products p ON p.id = pa.product_id
        WHERE p.category_id = ?
        """,
        (category_id,),
    ):
        products[product_id].attributes[key] = value
    return list(products.values())
