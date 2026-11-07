"""동률 정렬: 가격 요청, 텍스트 관련도(BM25 + 임베딩 RRF), 평점."""

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
    ("P1", "Studio earbuds", 4.9),
    ("P2", "Gaming earbuds with boom mic", 4.8),
    ("P3", "Running earbuds with ear hooks, secure fit, sweatproof", 4.7),
    ("P4", "Sport earbuds lightweight", 4.6),
]
TEXTS = {pid: f"{title}\n\n" for pid, title, _ in PRODUCTS}


def products():
    return [Product(pid, "headphones", title, None, 30.0, rating, 1000, None,
                    {"connection": "wireless", "form_factor": "in_ear", "anc": "yes" if n % 2 else "no"})
            for n, (pid, title, rating) in enumerate(PRODUCTS)]


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


def test_use_case_is_gone_and_old_facts_are_ignored():
    """주 용도는 정의에서 뺐다(2026-11-03). 예전 세션에 남은 그 사실은 엔진 입력·요약에서 빠진다."""
    catalog = build_catalog(SPEC, products())
    assert not SPEC.has_attribute("use_case") and "use_case" not in catalog.engine.attributes
    state = state_with(("connection", "wireless"), ("use_case", "sports"))
    assert catalog.preference(state).explicit == {"connection": "wireless"}
    assert not any("use_case" in line or "용도" in line for line in state.memory.describe(SPEC))


def test_ties_go_by_rating_without_conversation_text():
    catalog = build_catalog(SPEC, products())
    assert ordered(catalog, state_with(("connection", "wireless"))) == ["P1", "P2", "P3", "P4"]
    assert tie_order(catalog, state_with())[1] == "rating"


def test_text_relevance_orders_ties():
    catalog = build_catalog(SPEC, products(), text=TextIndex.from_texts(TEXTS))
    state = state_with(keywords=["secure fit", "running"])
    assert ordered(catalog, state)[0] == "P3"  # 평점은 가장 낮지만 대화와 가장 가깝다
    assert tie_order(catalog, state)[1] == "relevance"


def test_price_request_comes_before_everything_else():
    items = products()
    items[3].price = 10.0
    catalog = build_catalog(SPEC, items, text=TextIndex.from_texts(TEXTS))
    state = state_with(keywords=["gaming"])
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


# ---------------------------------------------------------------- 질의 임베딩 캐시·미리 계산

class CountingClient:
    """embed 호출 수를 세는 가짜 클라이언트. fail=True면 실패한다."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def embed(self, texts, *, model, dimensions):
        self.calls.append(texts[0])
        if self.fail:
            raise RuntimeError("down")
        return [[float(len(texts[0])), 1.0]]


def fresh_embedder(client, model="m-test"):
    from app.services.relevance import CachedEmbedder

    CachedEmbedder._cache.clear()
    return CachedEmbedder(client, model, 2)


def test_same_text_is_embedded_once_and_prefetch_is_reused():
    client = CountingClient()
    embed = fresh_embedder(client)
    embed.prefetch("wireless gym")
    assert embed("wireless gym") == [12.0, 1.0]  # 미리 계산한 결과 (같은 입력 → 같은 결과)
    assert embed("wireless gym") == [12.0, 1.0]
    assert client.calls == ["wireless gym"]
    embed("wireless gym, under $30")  # 글이 조금이라도 다르면 새로 계산
    assert client.calls == ["wireless gym", "wireless gym, under $30"]


def test_failed_embedding_is_not_cached():
    import pytest

    client = CountingClient(fail=True)
    embed = fresh_embedder(client, "m-fail")
    with pytest.raises(RuntimeError):
        embed("q")
    client.fail = False
    assert embed("q") == [1.0, 1.0]  # 실패는 남기지 않고 다시 계산한다
    assert client.calls == ["q", "q"]


def test_confirmation_turn_prefetches_the_query_used_for_recommendation():
    """확인 질문 턴에 미리 계산한 글과, 바뀐 게 없을 때 추천 턴에서 쓰는 글이 같다."""
    from app.services.chat import _ask_confirmation, _Understanding

    prefetched, embedded = [], []

    class Recorder:
        def prefetch(self, text):
            prefetched.append(text)

        def __call__(self, text):
            embedded.append(text)
            return [1.0, 0.0]

    index = TextIndex.from_texts(TEXTS, {pid: [1.0, 0.0] for pid in TEXTS})
    catalog = build_catalog(SPEC, products(), text=index, embed=Recorder())
    state = state_with(("connection", "wireless"), keywords=["running"])
    _ask_confirmation(catalog, state, catalog.preference(state), _Understanding())
    tie_order(catalog, state)  # 바뀐 게 없으면 다음 턴의 추천이 이 정렬을 쓴다
    assert prefetched and prefetched == embedded
