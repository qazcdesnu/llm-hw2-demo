"""IG 엔진의 데이터 모델. 카탈로그·DB·웹과 무관한 순수 데이터 구조.

@see docs/ig-engine.md
"""

from dataclasses import dataclass, field

# 사용자가 이 속성은 상관없다고 답한 경우의 값.
DONT_CARE = "dont_care"


@dataclass(frozen=True)
class AttributeDef:
    key: str
    values: tuple[str, ...]
    # False면 엔진이 먼저 묻지 않는다. 사용자가 말한 조건으로는 쓴다.
    askable: bool = True


@dataclass(frozen=True)
class Item:
    id: str
    # 속성 key -> 값. 없는 key는 unknown.
    attributes: dict[str, str]
    # 확률이 같을 때의 순서 (인기도 등). 확률 계산에는 쓰지 않는다.
    score: float = 0.0


@dataclass(frozen=True)
class Observation:
    """'목표 상품의 attribute는 value다'라는 증거와 그 확신도."""

    attribute: str
    value: str
    confidence: float


@dataclass
class Inference:
    value: str
    confidence: float
    reason: str = ""


@dataclass
class PreferenceState:
    """사용자 선호에 대해 지금까지 알게 된 것. 세션에 JSON으로 저장한다.

    belief는 저장하지 않고 매 턴 이 상태로 다시 계산한다. 그래서 정정은 값을 바꾸는 것만으로 끝난다.

    @docid core.preference-state
    @see docs/ig-engine.md#2-대화-상태
    """

    explicit: dict[str, str] = field(default_factory=dict)
    inferred: dict[str, Inference] = field(default_factory=dict)
    dont_care: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    # 사용자가 빼 달라고 한 상품 id ("1번 말고"). 후보에서 제외된다.
    excluded: list[str] = field(default_factory=list)

    def answer(self, attribute: str, value: str) -> None:
        """사용자가 직접 말한 조건. 같은 속성의 이전 답·추정을 대체한다(정정)."""
        self.inferred.pop(attribute, None)
        if value == DONT_CARE:
            self.explicit.pop(attribute, None)
            if attribute not in self.dont_care:
                self.dont_care.append(attribute)
        else:
            self.explicit[attribute] = value
            if attribute in self.dont_care:
                self.dont_care.remove(attribute)

    def infer(self, attribute: str, value: str, confidence: float, reason: str = "") -> None:
        """맥락에서 추정한 선호. 사용자가 직접 답한 속성이면 무시한다."""
        if attribute in self.explicit or attribute in self.dont_care:
            return
        self.inferred[attribute] = Inference(value, confidence, reason)

    def exclude(self, item_id: str) -> None:
        if item_id not in self.excluded:
            self.excluded.append(item_id)

    def mark_asked(self, attribute: str) -> None:
        if attribute not in self.asked:
            self.asked.append(attribute)

    def resolved(self) -> set[str]:
        """더 물을 필요가 없는 속성."""
        return set(self.explicit) | set(self.dont_care) | set(self.asked)

    def observations(self, noise: float) -> list[Observation]:
        """명시 답은 확신도 1-noise, 추정은 자체 확신도의 증거가 된다."""
        obs = [Observation(a, v, 1.0 - noise) for a, v in self.explicit.items()]
        obs += [Observation(a, inf.value, inf.confidence) for a, inf in self.inferred.items()]
        return obs

    def to_dict(self) -> dict:
        return {
            "explicit": dict(self.explicit),
            "inferred": {
                a: {"value": i.value, "confidence": i.confidence, "reason": i.reason}
                for a, i in self.inferred.items()
            },
            "dont_care": list(self.dont_care),
            "asked": list(self.asked),
            "excluded": list(self.excluded),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PreferenceState":
        return cls(
            explicit=dict(data.get("explicit", {})),
            inferred={a: Inference(**i) for a, i in data.get("inferred", {}).items()},
            dont_care=list(data.get("dont_care", [])),
            asked=list(data.get("asked", [])),
            excluded=list(data.get("excluded", [])),
        )
