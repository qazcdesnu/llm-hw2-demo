"""대화 메모리: 대화에서 알아낸 사실의 유일한 기록.

속성 값(근거 발화 포함)과 속성 밖의 사용 맥락을 남긴다. 속성 추출기(extractor.py)와 보기 버튼 답이 채우고,
IG 엔진이 쓰는 선호(PreferenceState)는 저장하지 않고 매 턴 이 기록에서 만든다(state.py).
"""

from dataclasses import asdict, dataclass, field

from app.catalog.spec import CategorySpec
from app.core.model import DONT_CARE

MAX_FACTS = 50
MAX_NOTES = 10


@dataclass
class Fact:
    """'목표 상품의 attribute는 value다'라는 사실 하나. value가 DONT_CARE면 상관없음."""

    attribute: str
    value: str
    quote: str  # 근거가 된 사용자 발화 (버튼이면 보기 이름)
    turn: int
    source: str = "said"  # said(말로 함, 속성 추출기) / button(보기 버튼)


@dataclass
class ConversationMemory:
    """@see docs/architecture.md#1-한눈에-보기
    @see docs/llm-parser.md#4-대화-메모리
    """
    facts: list[Fact] = field(default_factory=list)
    # 속성으로 표현되지 않는 사용 맥락 (예: "지하철 출퇴근 1시간", "안경을 씀")
    notes: list[str] = field(default_factory=list)

    def remember(self, fact: Fact) -> bool:
        """사실을 남긴다. 같은 속성의 마지막 값과 같으면 남기지 않고 False."""
        last = self.latest().get(fact.attribute)
        if last is not None and last.value == fact.value:
            return False
        self.facts.append(fact)
        del self.facts[:-MAX_FACTS]
        return True

    def add_notes(self, notes: list[str]) -> list[str]:
        """새 맥락만 더하고, 더한 것을 돌려준다."""
        added = []
        for note in (n.strip() for n in notes):
            if note and note not in self.notes:
                self.notes.append(note)
                added.append(note)
        del self.notes[:-MAX_NOTES]
        return added

    def latest(self) -> dict[str, Fact]:
        """속성별 가장 최근 사실 (정정하면 새 값이 이긴다)."""
        return {f.attribute: f for f in self.facts}

    def describe(self, spec: CategorySpec) -> list[str]:
        """LLM 프롬프트에 넣을 '지금까지 알게 된 것' (사람이 읽는 문장)."""
        lines = [f"{label_of(spec, f.attribute, f.value)} (근거: \"{f.quote}\")"
                 for f in self.latest().values()]
        return lines + [f"맥락: {n}" for n in self.notes]

    def to_dict(self) -> dict:
        return {"facts": [asdict(f) for f in self.facts], "notes": list(self.notes)}

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationMemory":
        return cls(facts=[Fact(**f) for f in data.get("facts", [])], notes=list(data.get("notes", [])))


def label_of(spec: CategorySpec, attribute: str, value: str) -> str:
    """'노이즈 캔슬링(ANC): 필요'처럼 사람이 읽는 속성=값."""
    attr = spec.attribute(attribute)
    if value == DONT_CARE:
        shown = "상관없음"
    else:
        shown = next((v.label for v in attr.values if v.value == value), value)
    return f"{attr.label}: {shown}"
