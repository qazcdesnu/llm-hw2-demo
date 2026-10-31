"""동률 정렬: 조건이 아닌 속성(주 용도), 텍스트 관련도(BM25 + 임베딩 RRF), 평점."""

import json

from app.catalog.repository import Product
from app.catalog.spec import load_category_spec
from app.services.chat import build_catalog, decide, tie_order
from app.services.memory import Fact
from app.services.relevance import TextIndex, product_text, relevance_scores
from app.services.state import ChatState

SPEC = load_category_spec("headphones")

# 모두 무선 인이어(ANC만 갈림) → 무선 조건으로는 동률. 평점은 P1 > P2 > P3 > P4
PRODUCTS = [
    ("P1", "Studio earbuds", "general", 4.9),
    ("P2", "Gaming earbuds with boom mic", "gaming", 4.8),
    ("P3", "Running earbuds with ear hooks, secure fit, sweatproof", "sports", 4.7),
    ("P4", "Sport earbuds lightweight", "sports", 4.6),
]
TEXTS = {pid: f"{title}\n\n" for pid, title, _, _ in PRODUCTS}


def products():
    return [Product(pid, "headphones", title, None, 30.0, rating, 1000, None,
                    {"connection": "wireless", "form_factor": "in_ear", "use_case": use,
                     "anc": "yes" if n % 2 else "no"})
            for n, (pid, title, use, rating) in enumerate(PRODUCTS)]


def state_with(*facts: tuple[str, str], keywords=()) -> ChatState:
    state = ChatState("headphones")
    for i, (attribute, value) in enumerate(facts, 1):
        state.memory.remember(Fact(attribute, value, f"q{i}", i))
    state.memory.add_keywords(list(keywords))
    return state


def ordered(catalog, state) -> list[str]:
    key, _ = tie_order(catalog, state)
    items = sorted(catalog.engine.items, key=key)
    return [i.id for i in items]


def test_use_case_is_not_an_engine_constraint():
    catalog = build_catalog(SPEC, products())
    assert "use_case" not in catalog.engine.attributes
    assert not SPEC.attribute("use_case").askable
    pref = catalog.preference(state_with(("connection", "wireless"), ("use_case", "sports")))
    assert pref.explicit == {"connection": "wireless"}


def test_ties_go_by_rating_without_conversation_text():
    catalog = build_catalog(SPEC, products())
    assert ordered(catalog, state_with(("connection", "wireless"))) == ["P1", "P2", "P3", "P4"]
    assert tie_order(catalog, state_with())[1] == "rating"


def test_soft_attribute_match_then_text_relevance():
    catalog = build_catalog(SPEC, products(), text=TextIndex.from_texts(TEXTS))
    state = state_with(("use_case", "sports"), keywords=["secure fit", "running"])
    # 용도가 맞는 P3·P4가 먼저, 그 안에서 키워드가 맞는 P3가 먼저
    assert ordered(catalog, state)[:2] == ["P3", "P4"]
    assert tie_order(catalog, state)[1] == "relevance"


def test_price_request_comes_before_everything_else():
    items = products()
    items[3].price = 10.0
    catalog = build_catalog(SPEC, items, text=TextIndex.from_texts(TEXTS))
    state = state_with(("use_case", "gaming"))
    state.price_order = "cheaper"
    assert ordered(catalog, state)[0] == "P4"
    assert tie_order(catalog, state)[1] == "cheaper"


def test_rrf_combines_bm25_and_embeddings():
    vectors = {"P1": [1.0, 0.0], "P2": [0.0, 1.0], "P3": [0.6, 0.8], "P4": [0.8, 0.6]}
    index = TextIndex.from_texts(TEXTS, vectors)
    scores = relevance_scores(index, "gaming mic", "게임할 때", embed=lambda text: [0.0, 1.0])
    assert max(scores, key=scores.get) == "P2"  # 둘 다 1위
    bm25_only = relevance_scores(index, "gaming mic", "", embed=None)
    assert set(bm25_only) == {"P2"}


def test_embedding_failure_falls_back_to_bm25():
    def broken(text):
        raise RuntimeError("down")

    index = TextIndex.from_texts(TEXTS, {"P1": [1.0, 0.0]})
    assert set(relevance_scores(index, "running", "달리기", embed=broken)) == {"P3"}


def test_recommendation_uses_tie_order_only_when_recommending():
    calls = []

    def embed(text):
        calls.append(text)
        return [1.0, 0.0]

    index = TextIndex.from_texts(TEXTS, {pid: [1.0, 0.0] for pid in TEXTS})
    catalog = build_catalog(SPEC, products(), text=index, embed=embed)
    state = state_with(keywords=["running"])
    decision, basis = decide(catalog, state, catalog.preference(state))
    assert basis is None and calls == []  # 질문 단계에서는 임베딩을 부르지 않는다
    state.finished = True
    decision, basis = decide(catalog, state, catalog.preference(state))
    assert basis == "relevance" and len(calls) == 1
    assert decision.action.items[0].item.id == "P3"


def test_product_text_skips_ranking_details():
    text = product_text("Earbuds", json.dumps(["Long battery"]),
                        json.dumps({"Item Weight": "5 g", "Best Sellers Rank": "#1"}))
    assert text == "Earbuds\nLong battery\nItem Weight: 5 g"
