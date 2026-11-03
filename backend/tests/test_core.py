import math

import pytest

from app.core.belief_tracker import compute_belief, entropy, likelihood, range_likelihood, set_likelihood
from app.core.engine import Ask, EngineConfig, IGEngine, Recommend
from app.core.model import (
    DONT_CARE, AttributeDef, Item, Observation, PreferenceState, RangeObservation, SetObservation,
)
from app.core.question_selector import (
    FixedOrderPolicy, InformationGainPolicy, RandomPolicy, expected_information_gain,
)
from app.core.recommender import recommend

CONN = AttributeDef("connection", ("wireless", "wired"))
FORM = AttributeDef("form", ("in", "over", "on", "open"))
ANC = AttributeDef("anc", ("yes", "no"))
ATTRS = {a.key: a for a in (CONN, FORM, ANC)}


def item(id: str, score: float = 0.0, **attrs) -> Item:
    return Item(id, attrs, score)


# 연결 방식은 반반, 착용 방식은 4종 균등, ANC는 1개만 yes
ITEMS = [
    item("a", connection="wireless", form="in", anc="yes"),
    item("b", connection="wireless", form="over", anc="no"),
    item("c", connection="wired", form="on", anc="no"),
    item("d", connection="wired", form="open", anc="no"),
]


# ------------------------------------------------------------- 가능도·belief

def test_likelihood_match_mismatch_unknown():
    obs = Observation("form", "in", 0.9)
    assert likelihood(ITEMS[0], obs, FORM) == pytest.approx(0.9)
    assert likelihood(ITEMS[1], obs, FORM) == pytest.approx(0.1 / 3)
    assert likelihood(item("x"), obs, FORM) == pytest.approx(1 / 4)


def test_low_confidence_is_raised_to_uniform():
    # 확신도가 1/k보다 낮으면 일치 상품이 불리해지므로 1/k로 올린다 → 정보 없음.
    obs = Observation("form", "in", 0.1)
    assert likelihood(ITEMS[0], obs, FORM) == pytest.approx(0.25)
    assert likelihood(ITEMS[1], obs, FORM) == pytest.approx(0.25)


def test_belief_update_and_unknown_items():
    items = ITEMS + [item("u")]  # u: 모든 속성 unknown
    belief = compute_belief(items, ATTRS, [Observation("connection", "wireless", 0.95)])
    assert belief[0] == pytest.approx(belief[1])
    assert belief[0] > belief[4] > belief[2]
    assert sum(belief) == pytest.approx(1.0)


def test_unknown_attribute_observation_is_ignored():
    assert compute_belief(ITEMS, ATTRS, [Observation("color", "red", 0.9)]) == [0.25] * 4


def test_entropy():
    assert entropy([0.25] * 4) == pytest.approx(2.0)
    assert entropy([1.0, 0.0]) == 0.0


# ------------------------------------------------------------- 기대 정보량

def test_information_gain_noiseless_matches_entropy_of_split():
    uniform = [0.25] * 4
    assert expected_information_gain(ITEMS, uniform, CONN, noise=0.0) == pytest.approx(1.0)
    assert expected_information_gain(ITEMS, uniform, FORM, noise=0.0) == pytest.approx(2.0)
    # 1:3으로 나뉘면 H(0.25) ≈ 0.811 bits
    h = -(0.25 * math.log2(0.25) + 0.75 * math.log2(0.75))
    assert expected_information_gain(ITEMS, uniform, ANC, noise=0.0) == pytest.approx(h)


def test_information_gain_is_zero_when_items_are_indistinguishable():
    same = [item("a", connection="wired"), item("b", connection="wired")]
    assert expected_information_gain(same, [0.5, 0.5], CONN, noise=0.05) == pytest.approx(0.0)
    unknown = [item("a"), item("b")]
    assert expected_information_gain(unknown, [0.5, 0.5], CONN, noise=0.05) == pytest.approx(0.0)


def test_noise_lowers_information_gain():
    uniform = [0.25] * 4
    assert expected_information_gain(ITEMS, uniform, CONN, 0.1) < expected_information_gain(ITEMS, uniform, CONN, 0.0)


# ------------------------------------------------------------- 상태

def test_state_answer_correction_and_dont_care():
    s = PreferenceState()
    s.answer("connection", "wired")
    s.answer("connection", "wireless")  # 정정
    s.answer("anc", "no")
    s.answer("form", DONT_CARE)
    s.answer("anc", DONT_CARE)  # 값 → 상관없음으로 정정
    assert s.explicit == {"connection": "wireless"}
    assert s.dont_care == ["form", "anc"]
    assert PreferenceState.from_dict(s.to_dict()) == s


def test_observations_use_noise():
    s = PreferenceState()
    s.answer("connection", "wired")
    s.answer("anc", DONT_CARE)  # 상관없음은 증거가 아니다
    assert s.observations(0.05) == [Observation("connection", "wired", 0.95)]


# ------------------------------------------------------------- 추천

def test_recommend_excludes_violations_unless_needed():
    s = PreferenceState()
    s.answer("connection", "wired")
    belief = [0.7, 0.1, 0.1, 0.1]  # 1위 a는 wireless라 조건 위반
    recs = recommend(ITEMS, belief, s, k=3)
    assert [r.item.id for r in recs] == ["c", "d", "a"]
    assert recs[0].matched == ["connection"] and recs[2].violated == ["connection"]


def test_recommend_tie_breaks_by_score():
    items = [item("a", 1.0), item("b", 3.0), item("c", 2.0)]
    assert [r.item.id for r in recommend(items, [1 / 3] * 3, PreferenceState())] == ["b", "c", "a"]


# ------------------------------------------------------------- 엔진

def run(engine: IGEngine, target: Item) -> tuple[list[str], Recommend]:
    """목표 상품의 속성대로 답하는 사용자와 대화. (질문 순서, 최종 추천)"""
    state, asked = PreferenceState(), []
    while True:
        decision = engine.step(state)
        if isinstance(decision.action, Recommend):
            return asked, decision.action
        attribute = decision.action.attribute
        asked.append(attribute)
        state.mark_asked(attribute)
        state.answer(attribute, target.attributes.get(attribute, DONT_CARE))


def test_ig_asks_most_informative_first_and_finds_target():
    engine = IGEngine(ITEMS, ATTRS)
    asked, rec = run(engine, ITEMS[2])
    assert asked[0] == "form"  # 2 bits > 1 bit > 0.81 bits
    assert rec.items[0].item.id == "c"
    assert rec.reason == "confident"


def test_stops_when_candidates_are_indistinguishable():
    twins = [item("a", connection="wired"), item("b", connection="wired"),
             item("c", connection="wireless")]
    # 상품이 3개뿐이면 Top-3 확률 합이 처음부터 1이라, 확신 종료 조건은 끈다.
    engine = IGEngine(twins, {"connection": CONN}, EngineConfig(confident_mass=1.1))
    asked, rec = run(engine, twins[0])
    assert asked == ["connection"]
    assert rec.reason == "no_informative_question"
    assert {r.item.id for r in rec.items[:2]} == {"a", "b"}


def test_tied_candidates_limitation():
    many = [item(str(i), connection="wired") for i in range(5)] + [item("w", connection="wireless")]
    engine = IGEngine(many, {"connection": CONN}, EngineConfig(confident_mass=1.1))
    _, rec = run(engine, many[0])
    tied = [lim for lim in rec.limitations if lim.code == "tied_candidates"]
    assert tied and tied[0].detail["count"] == 5


def test_budget_limits_questions():
    engine = IGEngine(ITEMS, ATTRS, EngineConfig(max_questions=1, confident_mass=1.1))
    asked, rec = run(engine, ITEMS[0])
    assert len(asked) == 1 and rec.reason == "budget"


def test_does_not_ask_resolved_or_unaskable_attributes():
    attrs = dict(ATTRS, form=AttributeDef("form", FORM.values, askable=False))
    engine = IGEngine(ITEMS, attrs, EngineConfig(confident_mass=1.1))
    state = PreferenceState()
    state.answer("connection", "wireless")
    decision = engine.step(state)
    assert isinstance(decision.action, Ask)
    assert decision.action.attribute == "anc"
    assert [c.attribute for c in decision.action.candidates] == ["anc"]


def test_policies_choose_from_same_candidates():
    engine = IGEngine(ITEMS, ATTRS)
    candidates = engine.step(PreferenceState()).action.candidates
    assert InformationGainPolicy().choose(candidates).attribute == "form"
    assert FixedOrderPolicy(["anc", "form"]).choose(candidates).attribute == "anc"
    picks = {RandomPolicy(seed).choose(candidates).attribute for seed in range(20)}
    assert picks == {"connection", "form", "anc"}
    assert RandomPolicy(3).choose(candidates) == RandomPolicy(3).choose(list(reversed(candidates)))


# ---------------------------------------------------------------- 숫자 범위 (가격)

PRICE = AttributeDef("price_band", ("low", "mid", "high"))


def priced(id: str, price: float | None, band: str) -> Item:
    return Item(id, {"price_band": band}, 0.0, {} if price is None else {"price_band": price})


def test_range_likelihood_uses_the_actual_number():
    """경계에 걸친 예산($15~25)은 구간(low/mid)이 아니라 실제 가격으로 판정한다."""
    obs = RangeObservation("price_band", 15, 25, 0.95)
    assert range_likelihood(priced("a", 18, "low"), obs, PRICE) == 0.95
    assert range_likelihood(priced("b", 24, "mid"), obs, PRICE) == 0.95  # 구간은 mid지만 범위 안
    assert range_likelihood(priced("c", 40, "mid"), obs, PRICE) == pytest.approx(0.025)
    assert range_likelihood(priced("d", None, "mid"), obs, PRICE) == pytest.approx(1 / 3)
    assert RangeObservation("price_band", None, 30, 0.95).contains(30)


def test_range_replaces_band_answer_and_counts_as_resolved():
    pref = PreferenceState()
    pref.answer("price_band", "mid")
    pref.answer_range("price_band", 15, 25)
    assert pref.explicit == {} and pref.ranges == {"price_band": (15, 25)}
    assert "price_band" in pref.resolved()  # 범위를 말하면 가격대 질문은 하지 않는다
    pref.answer("price_band", "high")  # 정정: 마지막 답이 이긴다
    assert pref.ranges == {} and pref.explicit == {"price_band": "high"}
    assert PreferenceState.from_dict(PreferenceState(ranges={"price_band": (None, 30)}).to_dict()).ranges == {
        "price_band": (None, 30)}


def test_recommend_filters_by_range_and_marks_unknown_price():
    items = [priced("cheap", 12, "low"), priced("edge", 24, "mid"), priced("pricey", 80, "high"),
             priced("noprice", None, "mid")]
    pref = PreferenceState(ranges={"price_band": (15, 25)})
    belief = compute_belief(items, {"price_band": PRICE}, pref.observations(0.05))
    ranked = recommend(items, belief, pref, k=4)
    assert ranked[0].item.id == "edge" and ranked[0].matched == ["price_band"]
    by_id = {r.item.id: r for r in ranked}
    assert by_id["noprice"].unknown == ["price_band"] and by_id["cheap"].violated == ["price_band"]


# ---------------------------------------------------------------- 여러 값 중 하나 (OR, NOT)

FIT = AttributeDef("form_factor", ("in_ear", "on_ear", "over_ear", "open_ear"))


def test_set_likelihood_accepts_any_listed_value():
    obs = SetObservation("form_factor", ("on_ear", "over_ear"), 0.95)
    assert set_likelihood(Item("a", {"form_factor": "on_ear"}), obs, FIT) == 0.95
    assert set_likelihood(Item("b", {"form_factor": "over_ear"}), obs, FIT) == 0.95
    assert set_likelihood(Item("c", {"form_factor": "in_ear"}), obs, FIT) == pytest.approx(0.05 / 3)
    assert set_likelihood(Item("d", {}), obs, FIT) == 0.25
    single = SetObservation("form_factor", ("in_ear",), 0.95)  # 한 값이면 보통 답과 같다
    assert set_likelihood(Item("c", {"form_factor": "in_ear"}), single, FIT) == likelihood(
        Item("c", {"form_factor": "in_ear"}), Observation("form_factor", "in_ear", 0.95), FIT)


def test_any_of_replaces_other_answers_and_filters_recommendations():
    pref = PreferenceState()
    pref.answer("form_factor", "in_ear")
    pref.answer_any_of("form_factor", ["on_ear", "over_ear"])
    assert pref.explicit == {} and pref.any_of == {"form_factor": ["on_ear", "over_ear"]}
    assert "form_factor" in pref.resolved()
    items = [Item("in", {"form_factor": "in_ear"}), Item("on", {"form_factor": "on_ear"}),
             Item("over", {"form_factor": "over_ear"}), Item("none", {})]
    belief = compute_belief(items, {"form_factor": FIT}, pref.observations(0.05))
    by_id = {r.item.id: r for r in recommend(items, belief, pref, k=4)}
    assert by_id["on"].matched == by_id["over"].matched == ["form_factor"]
    assert by_id["in"].violated == ["form_factor"] and by_id["none"].unknown == ["form_factor"]
    pref.answer("form_factor", "in_ear")  # 정정하면 한 값 답으로
    assert pref.any_of == {} and PreferenceState.from_dict(pref.to_dict()).explicit == {"form_factor": "in_ear"}
