"""평가할 추천 시스템 세 가지. 모두 '사용자 말 → 응답(+추천)' 같은 인터페이스를 갖는다.

| 이름 | 질문 고르기 | 추천 |
|---|---|---|
| ig | IG 엔진 (기대 정보량 최대) | IG 엔진 |
| random | 같은 시스템, 다음 질문 속성만 무작위 | IG 엔진 |
| llm | LLM이 자유롭게 대화 (IG·속성 엔진 없음) | 검색어 → 카탈로그 BM25 상위 30개 → LLM이 3개 선택 |

ig와 random은 파서·질문 작성기·추천기가 같고 질문 선택 정책만 다르다.

@see docs/evaluation.md#2-비교하는-시스템
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.core.engine import IGEngine
from app.core.question_selector import RandomPolicy
from app.core.recommender import recommend
from app.evaluation.agents import ChatAgent
from app.evaluation.search import CatalogSearch
from app.llm import LLMClient, LLMError
from app.services.chat import Catalog, Parser, QuestionWriter, handle_message, price_tie_key
from app.services.state import ChatState

SYSTEMS = ("ig", "random", "llm")
HISTORY_MESSAGES = 8
LLM_MAX_QUESTIONS = 5
SEARCH_K = 30


@dataclass
class SystemTurn:
    reply: str
    recommended: list[str] | None = None  # 추천을 했으면 상품 id (순서대로)
    extra: dict = field(default_factory=dict)


class EngineSystem:
    """우리 대화 시스템(ig) 또는 질문만 무작위로 고르는 비교군(random)."""

    def __init__(self, name: str, catalog: Catalog, parser: Parser | None, writer: QuestionWriter | None):
        self.name, self.catalog, self.parser, self.writer = name, catalog, parser, writer
        self.state = ChatState(catalog.spec.id)
        self.history: list[dict[str, str]] = []

    def respond(self, message: str) -> SystemTurn:
        result = handle_message(self.state, message, self.catalog, None, self.parser, self.writer,
                                self.history[-HISTORY_MESSAGES:])
        self.history += [{"role": "user", "content": message},
                         {"role": "assistant", "content": result.reply}]
        recs = [r.product_id for r in result.recommendations] or None
        return SystemTurn(result.reply, recs, {"asked": result.debug.selected_attribute})

    def target_rank(self, target_id: str) -> int | None:
        return target_rank(self.catalog, self.state, target_id)


def target_rank(catalog: Catalog, state: ChatState, target_id: str) -> int | None:
    """지금 상태에서 정답 상품의 순위 (전체 카탈로그 기준, 1부터). 사용자에게 보이는 정렬과 같다."""
    engine, pref = catalog.engine, state.preference()
    ranked = recommend(engine.items, engine.belief(pref), pref, k=len(engine.items),
                       tie_key=price_tie_key(catalog, state.price_order))
    excluded = set(pref.excluded)
    ids = [r.item.id for r in ranked if r.item.id not in excluded]
    return ids.index(target_id) + 1 if target_id in ids else None


class Pick(BaseModel):
    product_ids: list[str] = Field(description="후보 목록에서 고른 상품 id 3개, 잘 맞는 순서")
    message: str = Field(description="사용자에게 보낼 추천 메시지 (한국어, 짧게)")


class LLMSystem:
    """LLM이 자유롭게 대화하다가 추천하는 비교군. 추천은 검색어로 카탈로그를 찾아 그중에서 고른다.

    @docid eval.llm-baseline
    @see docs/evaluation.md#2-비교하는-시스템
    """

    name = "llm"

    def __init__(self, client: LLMClient, search: CatalogSearch, category_name: str):
        self.client, self.search = client, search
        self.questions = 0
        self.agent = ChatAgent(client, "\n".join([
            f"너는 {category_name} 추천 쇼핑 어시스턴트다. 사용자와 편한 반말로 대화한다.",
            "규칙:",
            "- 한 턴에 질문은 하나만 한다. 사용자의 선호를 가장 많이 좁혀 줄 질문을 고른다.",
            "- 충분히 파악했다고 판단되면 다른 말 없이 한 줄로 'RECOMMEND: <상품 검색용 영어 키워드>'만 출력한다.",
            "  (예: RECOMMEND: wireless earbuds noise cancelling under 50 dollars)",
            f"- 최대 {LLM_MAX_QUESTIONS}번 질문 안에 추천한다.",
        ]))

    def respond(self, message: str) -> SystemTurn:
        force = self.questions >= LLM_MAX_QUESTIONS
        out = self.agent.reply(message, note="이번에는 반드시 RECOMMEND: 한 줄로 답한다." if force else None)
        if not out.upper().startswith("RECOMMEND:"):
            self.questions += 1
            return SystemTurn(out)
        query = out.split(":", 1)[1].strip()
        hits = self.search.search(query, SEARCH_K)
        if not hits:  # 검색어가 하나도 안 맞으면 사용자 발화 전체로 다시 찾는다
            hits = self.search.search(" ".join(m["content"] for m in self.agent.history
                                               if m["role"] == "user"), SEARCH_K)
        ids = self._pick(hits)
        titles = "\n".join(f"{i}. {next(h.title for h in hits if h.id == pid)[:70]}"
                           for i, pid in enumerate(ids, 1))
        return SystemTurn(f"{query}\n{titles}", ids,
                          {"search_query": query, "candidate_ids": [h.id for h in hits]})

    def _pick(self, hits) -> list[str]:
        if not hits:
            return []
        candidates = "\n".join(
            f"{h.id} | {h.title[:100]} | {'$%.2f' % h.price if h.price is not None else '가격 정보 없음'}"
            for h in hits)
        conversation = "\n".join(f"{'사용자' if m['role'] == 'user' else '나'}: {m['content']}"
                                 for m in self.agent.history)
        try:
            pick = self.client.parse(
                [{"role": "user", "content": f"대화:\n{conversation}\n\n후보 (id | 상품명 | 가격):\n{candidates}"}],
                Pick, instructions="대화로 파악한 사용자 선호에 가장 잘 맞는 상품 3개를 후보 목록에서만 고른다.")
            valid = {h.id for h in hits}
            ids = list(dict.fromkeys(p for p in pick.product_ids if p in valid))
        except LLMError:
            ids = []
        for h in hits:  # 3개가 안 되면 검색 순위대로 채운다
            if len(ids) >= 3:
                break
            if h.id not in ids:
                ids.append(h.id)
        return ids[:3]


def make_system(name: str, catalog: Catalog, *, parser: Parser | None, writer: QuestionWriter | None,
                client: LLMClient, search: CatalogSearch, seed: int = 0):
    """평가용 시스템 하나 (에피소드마다 새로 만든다)."""
    if name == "ig":
        return EngineSystem("ig", catalog, parser, writer)
    if name == "random":
        engine = catalog.engine  # 상품·속성은 그대로, 질문 선택 정책만 바꾼다
        random_engine = IGEngine(engine.items, engine.attributes, engine.config, RandomPolicy(seed))
        return EngineSystem("random", Catalog(catalog.spec, catalog.products, random_engine), parser, writer)
    if name == "llm":
        return LLMSystem(client, search, catalog.spec.name)
    raise ValueError(f"unknown system: {name}")
