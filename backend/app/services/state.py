"""대화 상태: 세션에 JSON으로 저장되는 것과, 거기서 매 턴 만드는 엔진 입력.

저장하는 것
- memory: 대화에서 알아낸 사실의 유일한 기록 (속성 값·근거 발화, 사용 맥락)
- questions: 속성별 질문 기록 (물은 문장, 시도 횟수, 결과)
- 추천 조정: 보여준 추천, 제외한 상품, 가격 정렬

저장하지 않는 것
- PreferenceState: memory와 questions에서 매 턴 만든다(preference_of). 같은 사실을 두 곳에 쓰지 않기 위함이다.
- belief: 엔진이 PreferenceState로 매 턴 다시 계산한다.
"""

from dataclasses import asdict, dataclass, field

from app.core.model import PreferenceState
from app.services.memory import ConversationMemory

# 같은 속성을 몇 번까지 물을지. 답이 안 나오면 한 번 더(더 직접적으로) 묻고, 그래도 안 되면 넘어간다.
MAX_ASKS = 2


@dataclass
class QuestionRecord:
    """속성 하나에 대한 질문 기록.

    @see docs/llm-parser.md#6-질문에-답하지-않은-말
    """

    attribute: str
    text: str  # 마지막으로 물은 문장
    turn: int  # 마지막으로 물은 턴
    attempts: int = 1  # 물은 횟수
    # pending(답을 기다림) / answered(답이 나옴) / unanswered(답이 안 나옴, 다시 물을 수 있음)
    # / skipped(MAX_ASKS번 물어도 답이 안 나와 넘어감)
    status: str = "pending"


@dataclass
class ChatState:
    """sessions.state에 저장되는 대화 상태.

    @see docs/architecture.md#1-한눈에-보기
    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/architecture.md#세션-상태
    """

    category_id: str
    turn: int = 0
    memory: ConversationMemory = field(default_factory=ConversationMemory)
    questions: dict[str, QuestionRecord] = field(default_factory=dict)
    # True면 추천을 한 번 이상 보여준 상태. 이후 입력은 추천 목록 조정으로 처리한다.
    finished: bool = False
    last_recommended: list[str] = field(default_factory=list)
    # 사용자가 빼 달라고 한 상품 id ("1번 말고")
    excluded: list[str] = field(default_factory=list)
    # "더 싼/비싼 걸로": 조건이 똑같이 맞는 상품 사이의 가격 정렬 (cheaper / pricier / None)
    price_order: str | None = None

    # ------------------------------------------------------------ 질문

    @property
    def pending(self) -> QuestionRecord | None:
        """지금 답을 기다리는 질문."""
        return next((q for q in self.questions.values() if q.status == "pending"), None)

    def ask(self, attribute: str, text: str) -> QuestionRecord:
        record = self.questions.get(attribute)
        if record is None:
            record = self.questions[attribute] = QuestionRecord(attribute, text, self.turn)
        else:
            record.text, record.turn, record.attempts, record.status = (
                text, self.turn, record.attempts + 1, "pending")
        return record

    def close_pending(self, answered: bool) -> None:
        """대기 중인 질문을 닫는다. 답이 안 나왔으면 MAX_ASKS번 전까지는 다시 물을 수 있게 둔다."""
        record = self.pending
        if record is None:
            return
        if answered:
            record.status = "answered"
        else:
            record.status = "skipped" if record.attempts >= MAX_ASKS else "unanswered"

    # ------------------------------------------------------------ 엔진 입력

    def preference(self) -> PreferenceState:
        """메모리와 질문 기록으로 엔진이 쓰는 선호를 만든다.

        - explicit / dont_care: 속성별 마지막 사실
        - asked(다시 묻지 않음 + 질문 예산): 답이 나왔거나 넘어간 질문, 답을 기다리는 질문.
          답이 안 나와 다시 물을 수 있는 질문(unanswered)은 넣지 않는다.

        @docid chat.preference-of
        @see docs/architecture.md#3-대화-한-턴의-흐름
        @see docs/llm-parser.md#4-대화-메모리
        """
        pref = PreferenceState(excluded=list(self.excluded))
        for fact in self.memory.latest().values():
            pref.answer(fact.attribute, fact.value)
        pref.asked = [q.attribute for q in self.questions.values() if q.status != "unanswered"]
        return pref

    # ------------------------------------------------------------ 저장

    def to_dict(self) -> dict:
        return {
            "category_id": self.category_id,
            "turn": self.turn,
            "memory": self.memory.to_dict(),
            "questions": {k: asdict(q) for k, q in self.questions.items()},
            "finished": self.finished,
            "last_recommended": list(self.last_recommended),
            "excluded": list(self.excluded),
            "price_order": self.price_order,
        }

    @classmethod
    def from_dict(cls, data: dict, default_category: str) -> "ChatState":
        return cls(
            category_id=data.get("category_id", default_category),
            turn=data.get("turn", 0),
            memory=ConversationMemory.from_dict(data.get("memory", {})),
            questions={k: QuestionRecord(**q) for k, q in data.get("questions", {}).items()},
            finished=data.get("finished", False),
            last_recommended=list(data.get("last_recommended", [])),
            excluded=list(data.get("excluded", [])),
            price_order=data.get("price_order"),
        )
