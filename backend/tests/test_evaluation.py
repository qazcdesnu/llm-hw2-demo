"""평가: 검색, 지표, 에피소드 실행(가짜 LLM), 평가 API. 실제 LLM은 부르지 않는다."""

from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.catalog.repository import Product
from app.catalog.spec import load_category_spec
from app.db import connect
from app.evaluation import runner, scenarios as scenario_mod
from app.evaluation.judge import JudgeScores
from app.evaluation.runner import EvalContext, attribute_match, run_episode, summarize
from app.evaluation.scenarios import Scenario
from app.evaluation.search import CatalogSearch
from app.evaluation.systems import Pick
from app.main import create_app
from app.services.chat import build_catalog
from app.services.parser import ParsedConstraint, ParseResult
from app.services.questioner import WrittenQuestion
from tests.test_sessions import FakeParser, seed_catalog

SPEC = load_category_spec("headphones")

PRODUCTS = [
    Product("A", "headphones", "Sony wireless noise cancelling earbuds", None, 50.0, 4.5, 100, None,
            {"connection": "wireless", "form_factor": "in_ear", "anc": "yes"}),
    Product("B", "headphones", "Wired studio over ear headphones", None, 30.0, 4.0, 100, None,
            {"connection": "wired", "form_factor": "over_ear", "anc": "no"}),
    Product("C", "headphones", "Kids wired on ear headphones volume limit", None, 15.0, 4.0, 100, None,
            {"connection": "wired", "form_factor": "on_ear", "anc": "no"}),
]

SCENARIO = Scenario(id="c4-7", category_id="headphones", target_id="A", query="q", review="r",
                    persona=["무선 이어폰이 좋다", "소음을 막고 싶다"], opening="이어폰 찾고 있어",
                    brief="출퇴근용", target_attributes={"connection": "wireless", "form_factor": "in_ear",
                                                        "anc": "yes"})


class FakeLLM:
    """가상 사용자·LLM 비교군·심판 역할의 가짜 LLM."""

    def __init__(self):
        self.recommender_turns = 0

    def complete(self, messages, *, instructions=None):
        if "쇼핑몰 고객" in instructions:
            return "무선이 좋아"
        self.recommender_turns += 1
        return "어디서 쓸 거야?" if self.recommender_turns == 1 else "RECOMMEND: wireless earbuds"

    def parse(self, messages, schema, *, instructions=None):
        if schema is JudgeScores:
            return JudgeScores(fit=9, naturalness=4, efficiency=0, comment="ok")  # 범위 밖은 1~5로 자른다
        if schema is Pick:
            ids = [line.split(" | ")[0] for line in messages[0]["content"].splitlines() if " | " in line]
            return Pick(product_ids=list(reversed(ids)) + ["없는 id"], message="이거 어때")
        raise AssertionError(schema)


def context() -> EvalContext:
    docs = [(p.id, p.title, p.price, p.title) for p in PRODUCTS]
    parser = FakeParser({"무선이 좋아": ParseResult("shopping", [ParsedConstraint("connection", "wireless")])})
    writer = lambda spec, req: WrittenQuestion(f"[{req.attribute}]", "장면")  # noqa: E731
    return EvalContext(SPEC, build_catalog(SPEC, PRODUCTS), CatalogSearch(docs), FakeLLM(), parser, writer)


def test_search_ranks_by_bm25():
    search = CatalogSearch([(p.id, p.title, p.price, p.title) for p in PRODUCTS])
    hits = search.search("wireless noise cancelling", 2)
    assert [h.id for h in hits] == ["A"]  # 단어가 하나도 안 맞는 상품은 나오지 않는다
    assert [h.id for h in search.search("wired headphones")][:2] == ["B", "C"] or \
        set(h.id for h in search.search("wired headphones")) == {"B", "C"}
    assert search.search("zzz") == []


def test_attribute_match():
    assert attribute_match(SCENARIO, PRODUCTS[0].attributes) == 1.0
    assert attribute_match(SCENARIO, PRODUCTS[1].attributes) == 0.0


@pytest.mark.parametrize("system", ["ig", "random"])
def test_engine_episode(system):
    result = run_episode(context(), SCENARIO, system)
    assert result["system"] == system and result["recommended"]
    assert result["transcript"][0] == {"role": "user", "content": "이어폰 찾고 있어"}
    assert result["target_rank"] is not None  # 전체 카탈로그에서 정답 순위
    assert result["judge"]["fit"] == 5 and result["judge"]["efficiency"] == 1
    assert result["hit3"] == ("A" in result["recommended"][:3])


def test_llm_baseline_episode():
    result = run_episode(context(), SCENARIO, "llm")
    assert result["questions"] == 1
    assert result["recommended"] == ["A"]  # 검색 후보 안에서만 고른다
    assert result["hit1"] and result["extra"]["target_in_candidates"]
    assert result["target_rank"] is None


def test_summarize():
    rows = [
        {"system": "ig", "recommended": ["A"], "hit1": True, "hit3": True, "target_rank": 1,
         "attribute_match": 1.0, "questions": 2, "judge": {"fit": 5, "naturalness": 4, "efficiency": 3},
         "seconds": 10},
        {"system": "ig", "recommended": ["B"], "hit1": False, "hit3": False, "target_rank": 4,
         "attribute_match": 0.0, "questions": 4, "judge": {"fit": 1, "naturalness": 2, "efficiency": 3},
         "seconds": 20},
        {"system": "ig", "recommended": [], "error": "boom"},
    ]
    s = summarize(rows)["ig"]
    assert (s["episodes"], s["errors"], s["hit1"], s["mrr"], s["median_rank"]) == (2, 1, 0.5, 0.625, 4)
    assert (s["fit"], s["questions"], s["seconds"]) == (3, 3, 15)


# ---------------------------------------------------------------- API

@pytest.fixture
def api(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    seed_catalog(db_path)  # P1~P8
    scenario = Scenario(**{**SCENARIO.__dict__, "target_id": "P1"})
    monkeypatch.setattr(scenario_mod, "SCENARIO_DIR", tmp_path)
    scenario_mod.save_scenarios("headphones", [scenario])
    from app.api.sessions import get_parser, get_question_writer
    app = create_app(db_path)
    app.dependency_overrides[get_parser] = lambda: None
    app.dependency_overrides[get_question_writer] = lambda: None
    with TestClient(app) as client:
        yield client, db_path


def test_scenarios_hide_the_target(api):
    client, _ = api
    [s] = client.get("/api/eval/scenarios").json()
    assert s["id"] == "c4-7" and s["persona"] and "target_id" not in s and "target_attributes" not in s


def test_manual_scoring(api):
    client, _ = api
    sid = client.post("/api/sessions").json()["session_id"]
    body = {"session_id": sid, "scenario_id": "c4-7", "naturalness": 4, "satisfaction": 2}
    assert client.post("/api/eval/manual", json=body).status_code == 409  # 추천 전
    res = client.post(f"/api/sessions/{sid}/messages", json={"message": "추천해 줘"}).json()
    while res["question"]:
        q = res["question"]
        res = client.post(f"/api/sessions/{sid}/messages",
                          json={"message": "x", "answer": {"attribute": q["attribute"], "value": "dont_care"}}).json()
    result = client.post("/api/eval/manual", json=body).json()
    assert result["target"]["id"] == "P1" and result["target_rank"] >= 1
    assert result["recommended"] and result["naturalness"] == 4
    assert client.get("/api/eval/manual").json()[0]["scenario_id"] == "c4-7"
    assert client.post("/api/eval/manual", json={**body, "scenario_id": "nope"}).status_code == 404


def test_run_api_records_episodes(api, monkeypatch):
    client, db_path = api
    monkeypatch.setattr(runner, "load_context", lambda conn, category_id: context())
    monkeypatch.setattr(runner, "_spawn", lambda target, *args: target(*args))  # 테스트에서는 바로 실행
    scenario_mod.save_scenarios("headphones", [SCENARIO])
    assert client.post("/api/eval/runs", json={"systems": ["nope"]}).status_code == 422
    run_id = client.post("/api/eval/runs", json={"systems": ["ig", "llm"], "limit": 1}).json()["run_id"]
    run = client.get(f"/api/eval/runs/{run_id}").json()
    assert run["status"] == "done" and (run["done"], run["total"]) == (2, 2)
    assert set(run["summary"]) == {"ig", "llm"} and len(run["episodes"]) == 2
    assert client.get("/api/eval/runs").json()[0]["id"] == run_id
    with closing(connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM eval_episodes").fetchone()[0] == 2


def test_c4_candidates_filter_rating_and_review_length(tmp_path):
    path = tmp_path / "test.csv"
    long_review = "Great for the gym<br />" + "x" * 300
    path.write_text(
        "qid,query,item_id,user_id,ori_rating,ori_review\n"
        f"1,q,A,u,5.0,{long_review}\n"
        "2,q,A,u,5.0,Dad loves them\n"          # 짧음
        f"3,q,A,u,3.0,{long_review}\n"          # 긍정 아님
        f"4,q,Z,u,5.0,{long_review}\n",         # 카탈로그에 없음
        encoding="utf-8")
    rows = scenario_mod.c4_candidates({"A"}, path)
    assert [r["qid"] for r in rows] == ["1"]
    assert "<br" not in scenario_mod.review_text(rows[0])
    assert len(scenario_mod.c4_candidates({"A"}, path, min_rating=0, min_review_chars=0)) == 3
