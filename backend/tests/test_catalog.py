from contextlib import closing

import pytest
from pydantic import ValidationError

from app.catalog.extract import extract_attributes
from app.catalog.repository import list_categories, load_category, load_products, replace_products
from app.catalog.spec import CategorySpec, load_all_category_specs, load_category_spec
from app.db import connect, init_db


@pytest.fixture(scope="module")
def headphones() -> CategorySpec:
    return load_category_spec("headphones")


def make_item(**overrides) -> dict:
    item = {
        "parent_asin": "B000TEST",
        "title": "Acme Headphones",
        "features": ["Comfortable fit"],
        "description": [],
        "details": {},
        "categories": ["Electronics", "Headphones, Earbuds & Accessories", "Headphones & Earbuds"],
        "price": 30.0,
        "rating_number": 500,
        "average_rating": 4.3,
        "images": [{"variant": "MAIN", "large": "https://example.com/a.jpg"}],
    }
    item.update(overrides)
    return item


def values(spec: CategorySpec, item: dict) -> dict[str, str]:
    return {k: e.value for k, e in extract_attributes(spec, item).items()}


def test_all_category_specs_are_valid():
    specs = load_all_category_specs()
    assert "headphones" in specs


def test_details_rule_prefers_first_mapped_value(headphones):
    item = make_item(details={"Connectivity Technology": "Wireless, Wired", "Form Factor": "Over Ear"})
    v = values(headphones, item)
    assert v["connection"] == "wireless"
    assert v["form_factor"] == "over_ear"


def test_falls_back_to_later_rules(headphones):
    item = make_item(
        title="Acme Bluetooth Earbuds",
        categories=["Electronics", "Headphones, Earbuds & Accessories", "Headphones & Earbuds",
                    "Earbud Headphones"],
    )
    v = values(headphones, item)
    assert v["connection"] == "wireless"
    assert v["form_factor"] == "in_ear"


def test_keywords_band_and_defaults(headphones):
    item = make_item(
        price=129.99,
        features=["Active Noise Cancelling", "IPX5 sweatproof for running", "Built-in mic"],
    )
    v = values(headphones, item)
    assert v["anc"] == "yes"
    assert v["water_resistant"] == "yes"
    assert v["mic"] == "yes"
    assert v["use_case"] == "sports"
    assert v["price_band"] == "high"


def test_default_needs_features(headphones):
    assert values(headphones, make_item())["anc"] == "no"
    v = values(headphones, make_item(features=[]))
    assert "anc" not in v  # 설명이 없으면 unknown
    assert "connection" not in v


def test_unknown_if_blocks_default(headphones):
    # 애매한 "noise cancelling" 언급만 있으면 no로 단정하지 않는다.
    v = values(headphones, make_item(features=["CVC 8.0 Noise Cancelling for calls"]))
    assert "anc" not in v
    # 확실한 표현이 있으면 yes.
    v = values(headphones, make_item(features=["Active Noise Cancelling"]))
    assert v["anc"] == "yes"


def test_open_attribute_is_casefolded(headphones):
    assert values(headphones, make_item(details={"Brand": "SAMSUNG"}))["brand"] == "samsung"


def test_evidence_is_recorded(headphones):
    extracted = extract_attributes(headphones, make_item(features=["Hybrid Active Noise Cancelling"]))
    assert extracted["anc"].source == "keywords"
    assert "Active Noise" in extracted["anc"].evidence


def _spec_dict(**attr_overrides) -> dict:
    attr = {
        "key": "color", "label": "색상", "question": "색은?",
        "values": [{"value": "black", "label": "검정"}],
        "rules": [{"type": "keywords", "map": {"black": "black"}}],
    }
    attr.update(attr_overrides)
    return {
        "id": "test", "name": "테스트",
        "source": {"raw_file": "x.jsonl", "category": "X"},
        "attributes": [attr],
    }


def test_spec_rejects_undeclared_values():
    with pytest.raises(ValidationError, match="values에 없는 값"):
        CategorySpec.model_validate(_spec_dict(rules=[{"type": "keywords", "map": {"red": "red"}}]))


def test_spec_rejects_bad_regex():
    with pytest.raises(ValidationError, match="invalid regex"):
        CategorySpec.model_validate(_spec_dict(rules=[{"type": "keywords", "map": {"black": "("}}]))


def test_spec_requires_question_for_askable():
    with pytest.raises(ValidationError, match="question"):
        CategorySpec.model_validate(_spec_dict(question=None))


def test_repository_round_trip(tmp_path, headphones):
    db_path = tmp_path / "catalog.db"
    init_db(db_path)
    item = make_item(details={"Connectivity Technology": "Wired", "Brand": "Sony"})
    with closing(connect(db_path)) as conn:
        replace_products(conn, headphones, [(item, extract_attributes(headphones, item))])
        # 다시 빌드하면 기존 상품을 교체한다.
        assert replace_products(conn, headphones, [(item, extract_attributes(headphones, item))]) == 1
        conn.commit()

        assert load_category(conn, "headphones") == headphones
        assert [c.id for c in list_categories(conn)] == ["headphones"]
        [product] = load_products(conn, "headphones")
        assert product.id == "B000TEST"
        assert product.image_url == "https://example.com/a.jpg"
        assert product.attributes["connection"] == "wired"
        assert product.attributes["brand"] == "sony"


def test_allocate_is_proportional_and_exact():
    from app.catalog.sampling import allocate

    alloc = allocate({("a",): 600, ("b",): 300, ("c",): 95, ("d",): 5}, 150)
    assert sum(alloc.values()) == 150
    assert alloc[("a",)] == 90 and alloc[("b",)] == 45
    assert alloc[("d",)] <= 5


def test_stratified_sample_is_reproducible_and_respects_require_known(headphones):
    from app.catalog.sampling import stratified_sample
    from app.catalog.spec import SamplingSpec

    rows = []
    for i in range(200):
        item = make_item(
            parent_asin=f"B{i:04d}",
            title=f"Item {i} {'Bluetooth' if i % 3 else 'Wired'} Earbuds",
            details={"Form Factor": "In Ear" if i % 4 else "Over Ear"} if i % 10 else {},
            features=["fit"],
        )
        rows.append((item, extract_attributes(headphones, item)))
    spec = SamplingSpec(size=50, seed=7, stratify_by=["form_factor"], require_known=["form_factor"])

    first = stratified_sample(rows, spec)
    second = stratified_sample(list(reversed(rows)), spec)
    assert len(first) == 50
    assert [r[0]["parent_asin"] for r in first] == [r[0]["parent_asin"] for r in second]
    assert all("form_factor" in attrs for _, attrs in first)


def test_load_committed_catalog_json(tmp_path):
    """git에 포함된 카탈로그 JSON만으로 DB를 만들 수 있다 (원본 데이터 없이)."""
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))
    from load_catalog import CATALOG_DIR, load

    db_path = tmp_path / "loaded.db"
    expected = _json.loads((CATALOG_DIR / "headphones.json").read_text(encoding="utf-8"))
    assert load("headphones", db_path) == len(expected["products"]) == 150
    with closing(connect(db_path)) as conn:
        products = {p.id: p for p in load_products(conn, "headphones")}
    sample = expected["products"][0]
    assert products[sample["id"]].attributes == {k: a["value"] for k, a in sample["attributes"].items()}
    assert products[sample["id"]].image_url == sample["image_url"]
