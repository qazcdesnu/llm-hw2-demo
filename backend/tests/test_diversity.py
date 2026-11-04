"""추천 집중도·속성 일치 지표 (장난감 데이터)."""

import math

import pytest

from app.catalog.repository import Product
from app.evaluation import diversity as dv


def _product(pid: str, reviews: int, **attrs: str) -> Product:
    return Product(pid, "headphones", pid, None, 10.0, 4.0, reviews, None, attrs)


def test_distinct_ratio_and_coverage():
    lists = [["a", "b", "c"], ["a", "d", "e"]]
    assert dv.distinct_ratio(lists) == 5 / 6
    assert dv.coverage(lists, 100) == 0.05
    assert dv.distinct_ratio([]) is None


def test_exposure_counts_each_episode_once():
    assert dv.exposure([["a", "a", "b"], ["a"]]) == {"a": 2, "b": 1}


def test_gini_extremes():
    assert dv.gini([3, 3, 3, 3]) == pytest.approx(0.0)
    # 한 상품에 몰리면 (n−1)/n
    assert dv.gini([0, 0, 0, 10]) == pytest.approx(0.75)
    assert dv.gini([]) is None
    assert dv.gini([0, 0]) is None


def test_effective_items_and_top_share():
    counter = dv.exposure([["a"], ["b"], ["c"], ["d"]])
    assert dv.effective_items(counter) == pytest.approx(4.0)
    skewed = dv.exposure([["a"], ["a"], ["a"], ["b"]])
    assert 1 < dv.effective_items(skewed) < 2
    assert dv.top_share(skewed, 1) == 0.75
    assert dv.effective_items(dv.exposure([])) is None


def test_accumulation_curve_and_repeated_items():
    lists = [["a", "b"], ["b", "c"], ["a", "b"]]
    assert dv.accumulation_curve(lists) == [2, 3, 3]
    repeated = dv.repeated_items({"u1": ["a", "b"], "u2": ["b", "c"], "u3": ["a", "b"]})
    assert list(repeated) == ["b", "a"]  # 많이 나온 순
    assert repeated["b"] == ["u1", "u2", "u3"]


def test_pairwise_overlap_splits_by_need():
    by_user = {"u1": ["a", "b"], "u2": ["a", "c"], "u3": ["a", "d"]}
    signature = {"u1": "in_ear", "u2": "in_ear", "u3": "over_ear"}
    out = dv.pairwise_overlap(by_user, signature)
    assert out["same_need"] == {"pairs": 1, "overlapping_pairs": 1, "mean_jaccard": pytest.approx(1 / 3)}
    assert out["different_need"]["pairs"] == 2
    assert out["different_need"]["overlapping_pairs"] == 2


def test_pairwise_overlap_separates_repeats_of_same_scenario():
    by_user = {"r1:s1": ["a", "b"], "r2:s1": ["a", "b"], "r1:s2": ["c"], "r2:s2": ["a"]}
    signature = {"r1:s1": "x", "r2:s1": "x", "r1:s2": "y", "r2:s2": "y"}
    scenario = {"r1:s1": "s1", "r2:s1": "s1", "r1:s2": "s2", "r2:s2": "s2"}
    out = dv.pairwise_overlap(by_user, signature, scenario)
    assert out["same_scenario"]["pairs"] == 2
    assert out["same_scenario"]["mean_jaccard"] == pytest.approx(0.5)  # (1 + 0) / 2
    assert out["same_need"]["pairs"] == 0
    assert out["different_need"]["pairs"] == 4
    assert out["different_need"]["overlapping_pairs"] == 2  # r2:s2의 a가 s1 두 번과 겹침
    assert "same_scenario" not in dv.pairwise_overlap(by_user, signature)


def test_popularity_percentiles():
    products = {p.id: p for p in [_product("a", 100), _product("b", 200), _product("c", 300), _product("d", 400)]}
    assert dv.popularity_percentiles(["a", "d", "x"], products) == [0.25, 1.0]


def test_attribute_status_separates_unknown():
    target = {"connection": "wireless", "anc": "yes", "mic": "yes"}
    attrs = {"connection": "wireless", "anc": "no"}
    assert dv.attribute_status(target, attrs) == {"connection": dv.MATCH, "anc": dv.MISMATCH, "mic": dv.UNKNOWN}
    # 일치율은 runner.attribute_match와 같게 unknown을 다름으로 센다
    assert dv.match_rate(target, attrs) == pytest.approx(1 / 3)
    assert dv.match_rate(target, attrs, {"connection", "brand"}) == 1.0
    assert dv.match_rate(target, attrs, {"brand"}) is None


def test_chance_match_and_lift():
    target = {"connection": "wireless", "form_factor": "in_ear"}
    catalog = [_product("a", 1, connection="wireless", form_factor="in_ear"),
               _product("b", 1, connection="wireless", form_factor="over_ear"),
               _product("c", 1, connection="wired", form_factor="over_ear"),
               _product("d", 1, connection="wireless")]
    p0 = dv.chance_match(target, catalog)
    assert p0 == pytest.approx((1 + 0.5 + 0 + 0.5) / 4)
    assert dv.lift(1.0, p0) == pytest.approx(1.0)
    assert dv.lift(p0, p0) == pytest.approx(0.0)
    assert dv.lift(0.75, 0.5) == pytest.approx(0.5)
    assert dv.lift(None, p0) is None
    assert dv.lift(1.0, 1.0) is None


def test_jaccard_empty():
    assert dv.jaccard([], []) == 0.0
    assert math.isclose(dv.jaccard(["a", "b"], ["b", "c"]), 1 / 3)
