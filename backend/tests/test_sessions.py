from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.catalog.extract import Extracted
from app.catalog.repository import replace_products
from app.catalog.spec import load_category_spec
from app.core.model import DONT_CARE
from app.db import connect, init_db
from app.api.sessions import get_embedder, get_parser, get_question_writer
from app.main import create_app
from app.services.chat import match_text
from app.services.parser import ParsedConstraint, ParseResult
from app.services.questioner import WrittenQuestion

SPEC = load_category_spec("headphones")

# (id, connection, form_factor, anc, price_band, use_case)
PRODUCTS = [
    ("P1", "wireless", "in_ear", "yes", "high", "general"),
    ("P2", "wireless", "in_ear", "no", "low", "sports"),
    ("P3", "wireless", "over_ear", "yes", "high", "general"),
    ("P4", "wireless", "over_ear", "no", "mid", "gaming"),
    ("P5", "wired", "in_ear", "no", "low", "general"),
    ("P6", "wired", "over_ear", "no", "mid", "general"),
    ("P7", "wired", "on_ear", "no", "low", "kids"),
    ("P8", "wireless", "on_ear", "no", "mid", "sports"),
]


def seed_catalog(db_path) -> None:
    init_db(db_path)
    keys = ["connection", "form_factor", "anc", "price_band", "use_case"]
    rows = []
    for pid, *values in PRODUCTS:
        item = {"parent_asin": pid, "title": f"Headphone {pid}", "price": 30.0,
                "average_rating": 4.0, "rating_number": 100,
                "images": [{"variant": "MAIN", "large": f"https://img/{pid}.jpg"}]}
        rows.append((item, {k: Extracted(v, "test") for k, v in zip(keys, values)}))
    with closing(connect(db_path)) as conn:
        replace_products(conn, SPEC, rows)
        conn.commit()


class FakeParser:
    """발화 → 미리 정한 해석. 없는 발화는 아무 조건 없는 shopping. 호출 기록을 남긴다."""

    def __init__(self, table: dict[str, ParseResult] | None = None):
        self.table = table or {}
        self.calls = []

    def __call__(self, spec, message, ctx):
        self.calls.append({"message": message, "pending": ctx.pending, "current": ctx.current,
                           "question_text": ctx.question_text, "known": ctx.known})
        return self.table.get(message, ParseResult("shopping"))


class FakeWriter:
    """질문 요청 → '[속성] 질문'. 요청을 기록한다. fail=True면 실패(None)."""

    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []

    def __call__(self, spec, req):
        self.requests.append(req)
        return None if self.fail else WrittenQuestion(f"[{req.attribute}] 친구 질문", "장면")


def make_client(db_path, parser=None, writer=None):
    app = create_app(db_path)
    # 테스트에서는 실제 LLM을 부르지 않는다 (.env에 키가 있어도).
    app.dependency_overrides[get_parser] = lambda: parser
    app.dependency_overrides[get_question_writer] = lambda: writer
    app.dependency_overrides[get_embedder] = lambda: None
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    seed_catalog(db_path)
    with make_client(db_path) as c:
        yield c


def new_session(client) -> str:
    return client.post("/api/sessions").json()["session_id"]


def send(client, sid, message, answer=None):
    body = {"message": message}
    if answer:
        body["answer"] = {"attribute": answer[0], "value": answer[1]}
    return client.post(f"/api/sessions/{sid}/messages", json=body)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_first_message_asks_highest_ig_question(client):
    sid = new_session(client)
    res = send(client, sid, "헤드폰 추천해 줘").json()

    q = res["question"]
    assert "고르는 거 도와줄게" in res["reply"]
    assert q["options"][-1] == {"value": DONT_CARE, "label": "상관없음"}
    debug = res["debug"]
    assert debug["selected_attribute"] == q["attribute"]
    assert debug["expected_ig"] == max(s["expected_ig"] for s in debug["question_scores"])
    assert len(debug["top_candidates"]) == 5
    assert res["finished"] is False


def test_button_answers_until_recommendation(client):
    sid = new_session(client)
    res = send(client, sid, "추천해 줘").json()
    target = dict(zip(["connection", "form_factor", "anc", "price_band", "use_case"],
                      PRODUCTS[2][1:]))  # P3
    for _ in range(10):
        q = res["question"]
        if q is None:
            break
        value = target.get(q["attribute"], DONT_CARE)
        label = next(o["label"] for o in q["options"] if o["value"] == value)
        res = send(client, sid, label, (q["attribute"], value)).json()

    assert res["finished"] is True and res["question"] is None
    assert res["debug"]["stop_reason"] in {"budget", "confident", "no_informative_question"}
    recs = res["recommendations"]
    # 상품이 8개뿐이라 몇 번 만에 확신 종료되고, 착용 방식만 다른 P1과 동률일 수 있다.
    p3 = next(r for r in recs if r["product_id"] == "P3")
    assert p3["image_url"] == "https://img/P3.jpg"
    # 근거는 사용자가 답한 조건마다 하나씩 (P3는 모두 일치)
    explicit = res["debug"]["preferences"]["explicit"]
    assert explicit and len(p3["reasons"]) == len(explicit)
    for key in explicit:
        label = SPEC.attribute(key).label
        assert any(r.startswith(f"{label}: ") and "다름" not in r for r in p3["reasons"])

    # 파서 없이 추천 후 자유 입력은 이해할 수 없다고 안내한다
    after = send(client, sid, "또 추천해 줘").json()
    assert "LLM 연결이 필요" in after["reply"] and after["recommendations"] == []


def test_text_answer_is_matched_to_options(client):
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]
    option = q["options"][0]
    res = send(client, sid, f"{option['label']}이 좋아요").json()
    assert res["debug"]["preferences"]["explicit"] == {q["attribute"]: option["value"]}


def test_dont_care_text_and_unrecognized_text(client):
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]

    res = send(client, sid, "음 글쎄요").json()
    assert "잘 못 알아들었어" in res["reply"]
    assert res["question"]["attribute"] == q["attribute"]  # 같은 질문을 다시

    # LLM이 없으면 '상관없음' 같은 의미는 판정하지 않는다 (보기 버튼으로 고른다)
    res = send(client, sid, "그건 상관없어요").json()
    assert "잘 못 알아들었어" in res["reply"]
    assert res["debug"]["preferences"]["dont_care"] == []


def test_invalid_answer_is_rejected(client):
    sid = new_session(client)
    send(client, sid, "추천해 줘")
    assert send(client, sid, "x", ("connection", "bluetooth")).status_code == 422
    assert send(client, sid, "x", ("color", "red")).status_code == 422


def test_session_detail_restores_pending_question(client):
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["question"] == q
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["finished"] is False


def test_missing_catalog_returns_503(tmp_path):
    with make_client(tmp_path / "empty.db") as c:
        sid = new_session(c)
        res = send(c, sid, "추천해 줘")
        assert res.status_code == 503
        assert "build_catalog" in res.json()["detail"]


def test_unknown_session_and_empty_message(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert send(client, "nope", "hi").status_code == 404
    sid = new_session(client)
    assert client.post(f"/api/sessions/{sid}/messages", json={"message": ""}).status_code == 422


@pytest.mark.parametrize("attribute, text, expected", [
    ("connection", "무선이요", "wireless"),
    ("connection", "유선으로", "wired"),
    ("connection", "Bluetooth wireless", "wireless"),
    ("anc", "필요해요", "yes"),
    ("anc", "불필요해요", "no"),  # "필요"를 포함하지만 더 긴 "불필요"만 인정
    ("form_factor", "이어폰이 좋아", "in_ear"),
    ("form_factor", "아무거나", None),  # 의미 판정(상관없음)은 하지 않는다
    ("connection", "무선이든 유선이든", None),  # 둘 다 → 모호
    ("connection", "잘 모르겠어요", None),
])
def test_match_text(attribute, text, expected):
    assert match_text(SPEC.attribute(attribute), text) == expected


# ---------------------------------------------------------------- LLM 파서 연결 (가짜 파서)

def explicit(attribute, value, quote=""):
    return ParsedConstraint(attribute, value, quote)


@pytest.fixture
def parser_client(tmp_path):
    db_path = tmp_path / "test.db"
    seed_catalog(db_path)
    parser = FakeParser()
    with make_client(db_path, parser) as c:
        yield c, parser


def test_multiple_conditions_in_one_message(parser_client):
    client, parser = parser_client
    parser.table["무선이고 노캔 되는 걸로"] = ParseResult(
        "shopping", [explicit("connection", "wireless"), explicit("anc", "yes")])
    sid = new_session(client)
    res = send(client, sid, "무선이고 노캔 되는 걸로").json()

    prefs = res["debug"]["preferences"]
    assert prefs["explicit"] == {"connection": "wireless", "anc": "yes"}
    assert "좋아, 연결 방식 무선, 노이즈 캔슬링(ANC) 필요까지 챙길게" in res["reply"]
    # 이미 답한 속성은 묻지 않는다
    if res["question"]:
        assert res["question"]["attribute"] not in {"connection", "anc"}


def test_extracted_values_are_trusted_as_conditions(parser_client):
    """상황에서 뽑은 값도 확신도 없이 그대로 조건이 된다 (추천에서 조건 위반 상품을 뺀다)."""
    client, parser = parser_client
    parser.table["출퇴근용"] = ParseResult("shopping", [
        explicit("anc", "yes", "출퇴근"), explicit("connection", "wireless", "출퇴근")])
    sid = new_session(client)
    res = send(client, sid, "출퇴근용").json()
    assert res["debug"]["preferences"]["explicit"] == {"anc": "yes", "connection": "wireless"}
    assert {r["product_id"] for r in res["debug"]["top_candidates"][:2]} == {"P1", "P3"}


def test_non_shopping_requests_keep_the_question(parser_client):
    client, parser = parser_client
    parser.table["총 구하는 방법"] = ParseResult("unsafe")
    parser.table["노트북 추천"] = ParseResult("other_product")
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]

    res = send(client, sid, "총 구하는 방법").json()
    assert "도와줄 수 없어" in res["reply"]
    assert res["question"]["attribute"] == q["attribute"]
    assert res["debug"]["request_type"] == "unsafe"
    assert res["debug"]["preferences"]["explicit"] == {}

    res = send(client, sid, "노트북 추천").json()
    assert "만 추천할 수 있어" in res["reply"]


def test_unanswered_question_is_released_when_user_gives_other_info(parser_client):
    client, parser = parser_client
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]
    other = "anc" if q["attribute"] != "anc" else "connection"
    parser.table["그보다 이거"] = ParseResult("shopping", [explicit(other, "yes" if other == "anc" else "wired")])

    res = send(client, sid, "그보다 이거").json()
    prefs = res["debug"]["preferences"]
    assert other in prefs["explicit"]
    assert q["attribute"] not in prefs["explicit"]
    # 대기 중이던 질문은 '답 안 함'으로 닫혀 다시 물을 수 있다
    record = next(x for x in res["debug"]["memory"]["questions"] if x["attribute"] == q["attribute"])
    assert record["attempts"] == 1 or res["question"]["attribute"] == q["attribute"]
    assert q["attribute"] not in prefs["asked"] or res["question"]["attribute"] == q["attribute"]


def _finish(client, sid):
    res = send(client, sid, "추천해 줘").json()
    while res["question"]:
        q = res["question"]
        res = send(client, sid, "상관없음", (q["attribute"], DONT_CARE)).json()
    return res


def test_refine_after_recommendation(parser_client):
    client, parser = parser_client
    sid = new_session(client)
    first = _finish(client, sid)
    assert first["finished"] and first["recommendations"]
    assert "더 고려할 점" in first["reply"]

    parser.table["노캔 있는 무선으로"] = ParseResult(
        "shopping", [explicit("anc", "yes"), explicit("connection", "wireless")])
    res = send(client, sid, "노캔 있는 무선으로").json()

    assert res["finished"] and res["question"] is None
    assert res["debug"]["stop_reason"] == "refined"
    ids = [r["product_id"] for r in res["recommendations"]]
    assert ids[:2] == ["P1", "P3"] or set(ids[:2]) == {"P1", "P3"}  # 무선 + ANC는 P1, P3뿐
    # 파서에 현재 추천 목록이 전달된다 (상대 표현 해석용)
    assert parser.calls[-1]["current"] and len(parser.calls[-1]["current"]) == len(first["recommendations"])

    # 조건 변경: 무선 → 유선. 유선 + ANC 상품은 없어 조건 완화를 알린다
    parser.table["유선으로 바꿔줘"] = ParseResult("shopping", [explicit("connection", "wired")])
    res = send(client, sid, "유선으로 바꿔줘").json()
    assert res["debug"]["preferences"]["explicit"] == {"anc": "yes", "connection": "wired"}
    assert "조건을 모두 만족하는 상품이 부족" in res["reply"]

    # ANC를 상관없음으로 바꾸면 유선 상품이 올라온다
    parser.table["노캔은 상관없어"] = ParseResult("shopping", [explicit("anc", DONT_CARE)])
    res = send(client, sid, "노캔은 상관없어").json()
    wired = {p[0] for p in PRODUCTS if p[1] == "wired"}
    assert {r["product_id"] for r in res["recommendations"]} <= wired


def test_exclude_recommended_item(parser_client):
    client, parser = parser_client
    sid = new_session(client)
    first = _finish(client, sid)
    first_id = first["recommendations"][0]["product_id"]

    parser.table["1번 말고"] = ParseResult("shopping", exclude_ranks=[1])
    res = send(client, sid, "1번 말고").json()
    assert first_id not in [r["product_id"] for r in res["recommendations"]]
    assert res["debug"]["preferences"]["excluded"] == [first_id]
    assert "1번 상품 제외" in res["reply"]


def test_refine_with_nothing_understood_asks_for_more(parser_client):
    client, _ = parser_client
    sid = new_session(client)
    _finish(client, sid)
    res = send(client, sid, "음...").json()
    assert "조금만 더 말해 줄래" in res["reply"]
    assert res["recommendations"] == []


def test_cheaper_sorts_equally_matching_items_by_price(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    # 속성이 모두 같은 상품 4개, 가격만 다름 → 확률이 같아 정렬은 가격이 정한다
    rows = []
    for pid, price, rating in [("A", 50.0, 4.9), ("B", 10.0, 3.0), ("C", 30.0, 4.0), ("D", 90.0, 4.5)]:
        item = {"parent_asin": pid, "title": pid, "price": price, "average_rating": rating,
                "rating_number": 1000}
        rows.append((item, {"connection": Extracted("wireless", "test"),
                            "form_factor": Extracted("in_ear", "test")}))
    with closing(connect(db_path)) as conn:
        replace_products(conn, SPEC, rows)
        conn.commit()
    parser = FakeParser({"더 싼 걸로": ParseResult("shopping", price_order="cheaper"),
                         "더 좋은 걸로": ParseResult("shopping", price_order="pricier")})
    with make_client(db_path, parser) as client:
        sid = new_session(client)
        first = _finish(client, sid)
        assert [r["product_id"] for r in first["recommendations"]] == ["A", "D", "C"]  # 평점순

        res = send(client, sid, "더 싼 걸로").json()
        assert [r["product_id"] for r in res["recommendations"]] == ["B", "C", "A"]
        assert "더 저렴한 순" in res["reply"]
        assert "가격이 낮은 순으로 골랐어" in res["reply"]

        res = send(client, sid, "더 좋은 걸로").json()
        assert [r["product_id"] for r in res["recommendations"]] == ["D", "A", "C"]


def test_replies_follow_the_user_language(parser_client):
    """영어로 말하면 고정 문구·보기·추천 근거도 영어로 (카테고리 정의의 *_en)."""
    client, parser = parser_client
    parser.table["wireless please"] = ParseResult(
        "shopping", [explicit("connection", "wireless")], language="en")
    sid = new_session(client)
    res = send(client, sid, "wireless please").json()
    assert "Got it, I'll keep Connection: Wireless in mind." in res["reply"]
    assert res["question"]["text"] in res["reply"]
    assert all(o["label"].isascii() for o in res["question"]["options"])
    assert res["question"]["options"][-1]["label"] == "Doesn't matter"


def test_repeated_condition_is_not_acknowledged_again(parser_client):
    client, parser = parser_client
    parser.table["무선으로"] = ParseResult("shopping", [explicit("connection", "wireless")])
    parser.table["무선이고 노캔도"] = ParseResult(
        "shopping", [explicit("connection", "wireless"), explicit("anc", "yes")])
    sid = new_session(client)
    send(client, sid, "무선으로")
    res = send(client, sid, "무선이고 노캔도").json()
    assert "좋아, 노이즈 캔슬링(ANC) 필요까지 챙길게." in res["reply"]
    assert "연결 방식" not in res["reply"].split("\n")[0]


# ---------------------------------------------------------------- 메모리·질문 작성기

@pytest.fixture
def chat(tmp_path):
    db_path = tmp_path / "test.db"
    seed_catalog(db_path)
    parser, writer = FakeParser(), FakeWriter()
    with make_client(db_path, parser, writer) as c:
        yield c, parser, writer


def test_memory_keeps_facts_with_quotes_and_notes(chat):
    client, parser, _ = chat
    parser.table["지하철 출퇴근용, 무선으로"] = ParseResult(
        "shopping",
        [explicit("connection", "wireless", "무선으로")], notes=["지하철 출퇴근"])
    sid = new_session(client)
    res = send(client, sid, "지하철 출퇴근용, 무선으로").json()

    memory = res["debug"]["memory"]
    facts = {f["attribute"]: f for f in memory["facts"]}
    assert facts == {"connection": {"attribute": "connection", "value": "wireless", "quote": "무선으로",
                                    "turn": 1, "source": "said"}}
    assert memory["notes"] == ["지하철 출퇴근"]
    # 엔진 입력은 메모리에서 만든다
    assert res["debug"]["preferences"]["explicit"] == {"connection": "wireless"}

    # 다음 턴 파서는 메모리 요약과 직전 질문 문장을 맥락으로 받는다
    send(client, sid, "음")
    ctx = parser.calls[-1]
    assert ctx["question_text"] == res["question"]["text"]
    assert '연결 방식: 무선 (근거: "무선으로")' in ctx["known"]
    assert "맥락: 지하철 출퇴근" in ctx["known"]


def test_button_answer_is_remembered(chat):
    client, _, _ = chat
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]
    option = q["options"][0]
    res = send(client, sid, option["label"], (q["attribute"], option["value"])).json()
    fact = res["debug"]["memory"]["facts"][-1]
    assert (fact["attribute"], fact["value"], fact["source"], fact["quote"]) == \
        (q["attribute"], option["value"], "button", option["label"])


def test_writer_phrases_the_question(chat):
    client, parser, writer = chat
    parser.table["무선으로"] = ParseResult("shopping", [explicit("connection", "wireless")])
    sid = new_session(client)
    res = send(client, sid, "무선으로").json()

    q = res["question"]
    assert res["reply"] == q["text"] == f"[{q['attribute']}] 친구 질문"
    assert res["debug"]["question_source"] == "llm" and res["debug"]["question_approach"] == "장면"
    req = writer.requests[-1]
    assert req.attribute == q["attribute"] and req.first_turn and not req.retry
    assert req.message == "무선으로" and req.learned == ["연결 방식 무선"] and req.history == []
    assert any(line.startswith("연결 방식: 무선") for line in req.known)

    # 다음 턴의 질문 작성기는 최근 대화(DB 기록)를 받고, 세션 조회도 같은 문장을 돌려준다
    send(client, sid, q["options"][-1]["label"], (q["attribute"], q["options"][-1]["value"]))  # 상관없음
    assert writer.requests[-1].history == [
        {"role": "user", "content": "무선으로"}, {"role": "assistant", "content": res["reply"]}]
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["question"]["text"] == writer.requests[-1].attribute.join(["[", "] 친구 질문"])


def test_writer_failure_falls_back_to_fixed_question(tmp_path):
    db_path = tmp_path / "test.db"
    seed_catalog(db_path)
    with make_client(db_path, FakeParser(), FakeWriter(fail=True)) as client:
        sid = new_session(client)
        res = send(client, sid, "추천해 줘").json()
    q = res["question"]
    assert q["text"] == SPEC.attribute(q["attribute"]).question
    assert res["debug"]["question_source"] == "template"


def test_unrelated_reply_is_asked_again_then_skipped(chat):
    """질문과 무관한 말에 '보기에서 고르라'고 하지 않는다. 한 번 더 직접적으로 묻고, 또 안 되면 넘어간다."""
    client, _, writer = chat
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]
    attribute = q["attribute"]

    res = send(client, sid, "근데 요즘 다들 뭐 써?").json()
    assert "보기에서 골라" not in res["reply"]
    if res["question"]["attribute"] == attribute:  # 엔진이 같은 속성을 다시 고르면 더 직접적으로
        assert writer.requests[-1].retry and writer.requests[-1].unclear
        res = send(client, sid, "글쎄").json()
    assert attribute in res["debug"]["preferences"]["asked"]
    assert res["question"] is None or res["question"]["attribute"] != attribute


def test_llm_judgement_is_not_overridden_by_text_rules(chat):
    """LLM이 있으면 보기 이름이 들어 있어도 규칙으로 판정하지 않는다. LLM이 찾은 것만 반영한다."""
    client, _, _ = chat
    sid = new_session(client)
    q = send(client, sid, "추천해 줘").json()["question"]
    for text in (q["options"][0]["label"], "상관없어"):  # 가짜 파서는 아무 조건도 돌려주지 않는다
        res = send(client, sid, text).json()
        prefs = res["debug"]["preferences"]
        assert q["attribute"] not in prefs["explicit"] and q["attribute"] not in prefs["dont_care"]


def test_off_topic_with_writer_continues_the_conversation(chat):
    client, parser, writer = chat
    parser.table["오늘 날씨 좋다"] = ParseResult("off_topic")
    sid = new_session(client)
    send(client, sid, "추천해 줘")
    res = send(client, sid, "오늘 날씨 좋다").json()
    assert res["question"] is not None and res["debug"]["request_type"] == "off_topic"
    assert writer.requests[-1].unclear


def test_serves_built_frontend_when_present(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>built-ui</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    with TestClient(create_app(tmp_path / "t.db", frontend_dist=dist)) as c:
        assert "built-ui" in c.get("/").text
        assert c.get("/assets/app.js").status_code == 200
        assert c.get("/api/health").json() == {"status": "ok"}  # API가 우선
        assert c.post("/api/sessions").status_code == 201


def test_no_frontend_mount_without_build(tmp_path):
    with TestClient(create_app(tmp_path / "t.db", frontend_dist=tmp_path / "missing")) as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 200
