"""대화 상태: 메모리에서 엔진 입력 만들기, 질문 기록의 생명주기."""

from app.core.model import DONT_CARE
from app.services.memory import Fact
from app.services.state import MAX_ASKS, ChatState


def state_with(*facts: tuple[str, str]) -> ChatState:
    state = ChatState("headphones")
    for i, (attribute, value) in enumerate(facts, 1):
        state.memory.remember(Fact(attribute, value, f"q{i}", i))
    return state


def test_preference_is_built_from_the_latest_facts():
    state = state_with(("connection", "wired"), ("anc", "yes"), ("connection", "wireless"),
                       ("anc", DONT_CARE))
    state.excluded = ["P1"]
    pref = state.preference()
    assert pref.explicit == {"connection": "wireless"}  # 정정은 마지막 값
    assert pref.dont_care == ["anc"]
    assert pref.excluded == ["P1"]


def test_same_value_is_not_remembered_twice():
    state = state_with(("connection", "wired"))
    assert not state.memory.remember(Fact("connection", "wired", "다시", 2))
    assert len(state.memory.facts) == 1


def test_question_lifecycle():
    state = ChatState("headphones")
    state.ask("anc", "어디서 들어?")
    assert state.pending.attribute == "anc"
    assert state.preference().asked == ["anc"]  # 답을 기다리는 동안은 다시 묻지 않는다

    state.close_pending(answered=False)  # 답이 안 나옴 → 다시 물을 수 있다
    assert state.pending is None and state.preference().asked == []

    for _ in range(MAX_ASKS - 1):
        state.ask("anc", "시끄러운 데서도 써?")
        state.close_pending(answered=False)
    record = state.questions["anc"]
    assert (record.attempts, record.status, record.text) == (MAX_ASKS, "skipped", "시끄러운 데서도 써?")
    assert state.preference().asked == ["anc"]  # 넘어간 질문은 다시 묻지 않는다(예산도 씀)

    state.ask("mic", "통화도 해?")
    state.close_pending(answered=True)
    assert state.questions["mic"].status == "answered"


def test_round_trip():
    state = state_with(("connection", "wired"))
    state.memory.add_notes(["안경 착용"])
    state.ask("anc", "어디서 들어?")
    state.finished, state.last_recommended, state.price_order = True, ["P1"], "cheaper"
    assert ChatState.from_dict(state.to_dict(), "x") == state
