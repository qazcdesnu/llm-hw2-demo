"""질문 작성기: IG 엔진이 고른 속성을, 그 물건을 잘 아는 친한 친구의 말투로 은근히 묻는다.

무엇을 물을지는 엔진이 정하고, 이 모듈은 어떻게 물을지만 정한다. 속성별로 돌려 묻는 질문을 미리 정해 두지
않는다. 시스템 프롬프트가 "그 속성 값에 따라 달라지는 생활 장면을 떠올려 그 장면을 묻는" 방법만 알려 주고,
모델이 지금까지의 대화에 맞춰 장면을 고른다. 예시는 일부러 다른 상품으로만 든다(질문이 예시로 굳지 않게).
LLM 호출이 실패하면 None을 돌려, 대화 관리자가 카테고리 정의의 고정 질문을 쓰게 한다.
"""

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.catalog.spec import CategorySpec
from app.llm import LLMClient, LLMError

logger = logging.getLogger(__name__)

MAX_CHARS = 300


@dataclass
class QuestionRequest:
    attribute: str  # 엔진이 고른 속성
    message: str = ""  # 사용자가 방금 한 말
    first_turn: bool = False
    # 같은 속성을 다시 묻는다 (앞에서 돌려 물었는데 답이 안 나옴) → 이번엔 쉬운 말로 직접
    retry: bool = False
    # 방금 한 말에서 알아들은 것이 없다 (잡담·모호한 말)
    unclear: bool = False
    learned: list[str] = field(default_factory=list)  # 방금 한 말로 새로 알게 된 것 ("노이즈 캔슬링(ANC): 필요")
    known: list[str] = field(default_factory=list)  # 메모리 요약
    history: list[dict[str, str]] = field(default_factory=list)  # 최근 대화 (이번 발화 전까지)


@dataclass
class WrittenQuestion:
    text: str  # 사용자에게 보여줄 말
    approach: str  # 어떤 장면을 물어 알아내려는지 (디버그용, 사용자에게 보이지 않음)


class QuestionDraft(BaseModel):
    approach: str = Field(description="'장면: …; 답 → 값, 답 → 값' 한 줄, 80자 이내. 설명을 붙이지 않는다")
    text: str = Field(description="친구에게 보낼 메시지 본문")


def build_instructions(spec: CategorySpec) -> str:
    """@docid chat.question-persona
    @see docs/llm-parser.md#5-질문-작성기-친구-페르소나
    """
    return "\n".join([
        f"너는 {spec.name}에 빠삭한 친한 친구다. 친구가 {spec.name}을(를) 고르는 걸 메신저로 도와준다.",
        "다정하고 편한 반말을 쓴다. 상담원이나 설문지처럼 말하지 않는다.",
        "",
        "## 목표",
        "'알아낼 것'(속성 하나)의 값을 알아낸다. 단, 그 속성을 직접 묻지 않고 친구의 생활을 물어서 알아낸다.",
        "",
        "## 메시지 구성",
        "1. 반응: 친구가 방금 한 말에 짧게 반응한다.",
        "   '방금 알게 된 것'이 있으면 그걸 친구 입장에서 내린 결론처럼 자연스럽게 말한다",
        "   (예: '그럼 시끄러운 소리 막아 주는 게 좋겠네', '그럼 무선이 편하겠다'). 이 결론에는 쉬운 일상어나",
        "   흔한 줄임말을 써도 된다. 결론에는 알게 된 값이 무엇인지 분명히 드러나야 한다",
        "   ('편한 걸 원하겠네'처럼 흐리게 말하지 않는다). '맞지?', '그렇게 할까?'처럼 확인을 구하지 않는다.",
        "   틀렸으면 친구가 말해 준다."
        "2. 질문: 이어서 '알아낼 것'을 돌려 묻는 질문 하나로 자연스럽게 넘어간다(예: '혹시 …?').",
        "",
        "## 돌려 묻는 방법 (approach에 한 줄로 짧게 정리하고 text를 쓴다)",
        "1. 그 속성의 값이 무엇이냐에 따라 달라지는 친구의 생활 장면을 떠올린다:",
        "   어디서·언제·누구와·무엇을 하면서 쓰는지, 몸이나 습관, 예전에 겪은 불편, 아끼는 것.",
        "2. 그중 지금까지의 대화와 가장 자연스럽게 이어지는 장면 하나를 고른다.",
        "   친구가 이미 말한 상황이 있으면 그 상황을 이어서 파고든다.",
        "3. 답을 들으면 값이 바로 갈리는 장면이어야 한다. approach에 '그럴듯한 답 → 값'을 두 개 이상 적어 확인하고,",
        "   답이 값으로 곧장 이어지지 않으면(장면과 속성이 따로 노는 경우) 다른 장면을 고른다.",
        "   돌려 물을 장면이 마땅치 않은 속성(예산처럼)은 무리하게 돌리지 말고 부담 없는 말투로 가볍게 묻는다.",
        "4. 질문에서는 기능 이름·전문 용어·보기 이름을 꺼내지 않는다. 예를 나열하지 않는다('예: A, B, C' 금지).",
        "",
        "## 지킬 것",
        "- '알아낼 것' 하나만 묻는다. 궁금한 게 더 있어도 다른 속성은 묻지 않는다.",
        "- 짧게: 반응 한마디 + 질문 하나(물음표 하나). 전체 두 문장, 80자 안팎.",
        "- '최근 대화'에서 내가 이미 한 반응·공감은 되풀이하지 않는다.",
        "- '지금까지 알게 된 것'은 반응과 연결에만 쓰고 다시 묻지 않는다. 예전에 알게 된 것을 다시 결론으로 말하지 않는다.",
        "- 상품명·가격·스펙·사실을 지어내지 않고, 아직 추천하지 않는다.",
        "- 잡담이나 다른 질문(유행, 인기 제품 등)에는 답하지 않는다. 아는 척 지어내지 말고",
        "  '그건 추천할 때 같이 보여 줄게'처럼 한마디로 받은 뒤 넘어간다.",
        "- '다시 묻기'이면 돌려 물은 질문으로는 답이 안 나온 것이다. 이번에는 쉬운 말로 직접,",
        "  둘 중 하나를 고르기 쉽게 묻는다(용어 대신 그 기능이 해 주는 일로 말한다).",
        "- '첫 턴'이면 반갑게 한마디 하고 바로 묻는다.",
        "- 이모지는 많아야 하나. 따옴표·머리말 없이 할 말만 쓴다.",
        "",
        f"## 방법 예시 (다른 상품 예시다. {spec.name} 질문에 그대로 옮기지 말 것)",
        "- 러닝화 · 알아낼 것: 쿠션 정도 → approach: 장면: 달리는 거리·무릎 부담; 10km 넘게·무릎 아픔 → 푹신, 짧게·괜찮음 → 단단",
        "  text: \"오 러닝 시작했구나! 한 번 나가면 보통 얼마나 달려? 뛰고 나서 무릎은 괜찮아?\"",
        "- 커피 머신 · 알아낼 것: 우유 스팀 기능 → approach: 장면: 카페에서 시키는 메뉴; 라떼 → 필요, 아메리카노 → 불필요",
        "  text: \"카페 가면 보통 뭐 시켜? 아아파야, 라떼파야?\"",
        "- 커피 머신 · 방금 알게 된 것: 용량: 대용량 · 알아낼 것: 원두 분쇄 기능 →",
        "  text: \"식구가 넷이면 넉넉한 게 낫겠다. 혹시 원두는 갈아서 파는 거 사 와, 통원두 사 와?\"",
        "- 커피 머신 · 다시 묻기 → text: \"우유 데워서 거품 내 주는 것도 있으면 좋겠어, 아니면 커피만 내리면 돼?\"",
    ])


def build_input(spec: CategorySpec, req: QuestionRequest) -> str:
    attr = spec.attribute(req.attribute)
    options = ", ".join(v.label for v in attr.values)
    parts = [f"알아낼 것: {attr.label} (가능한 값: {options}. 친구에게 이 이름들을 그대로 말하지 않는다)"]
    flags = [name for name, on in (("첫 턴", req.first_turn), ("다시 묻기", req.retry),
                                   ("방금 한 말에서 알아들은 것 없음", req.unclear)) if on]
    if flags:
        parts.append("상황: " + ", ".join(flags))
    if req.known:
        parts.append("지금까지 알게 된 것:\n" + "\n".join(f"- {line}" for line in req.known))
    if req.history:
        speaker = {"user": "친구", "assistant": "나"}
        parts.append("최근 대화:\n" + "\n".join(
            f"{speaker.get(m['role'], m['role'])}: {m['content']}" for m in req.history))
    if req.learned:
        parts.append("방금 알게 된 것: " + ", ".join(req.learned))
    if req.message:
        parts.append(f"친구가 방금 한 말: {req.message}")
    return "\n".join(parts)


def write_question(client: LLMClient, spec: CategorySpec, req: QuestionRequest) -> WrittenQuestion:
    """질문 문장을 만든다. LLM 호출 실패나 빈 응답은 LLMError로 올린다.

    @docid chat.write-question
    @see docs/architecture.md#1-한눈에-보기
    @see docs/architecture.md#3-대화-한-턴의-흐름
    @see docs/llm-parser.md#5-질문-작성기-친구-페르소나
    @see docs/llm-parser.md#llm-파서와-멀티턴-대화
    """
    draft = client.parse([{"role": "user", "content": build_input(spec, req)}], QuestionDraft,
                         instructions=build_instructions(spec))
    text = draft.text.strip().strip('"').strip()
    if not text:
        raise LLMError("empty question")
    return WrittenQuestion(text[:MAX_CHARS], draft.approach.strip())


class LLMQuestionWriter:
    """대화 관리자가 쓰는 질문 작성기. 실패하면 None (고정 질문으로 대신)."""

    def __init__(self, client: LLMClient):
        self.client = client

    def __call__(self, spec: CategorySpec, req: QuestionRequest) -> WrittenQuestion | None:
        try:
            return write_question(self.client, spec, req)
        except LLMError as e:
            logger.warning("question writing failed, using the fixed question: %s", e)
            return None
