"""@see docs/architecture.md#1-한눈에-보기"""

import json
import sqlite3
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from app.catalog.repository import load_category, load_products
from app.config import settings
from app.db import get_db
from app.llm import LLMNotConfiguredError, chat_client, chat_models, get_llm_client
from app.schemas import (
    ChatRequest, ChatResponse, Message, ModelChoice, ModelList, SessionCreated, SessionDetail,
)
from app.services.chat import (
    Catalog, InvalidAnswer, Parser, QuestionWriter, build_catalog, handle_message,
)
from app.services.responses import build_question
from app.services.state import ChatState
from app.services.parser import LLMParser
from app.services.questioner import LLMQuestionWriter
from app.services.relevance import Embedder, embedder_for, load_text_index

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

HISTORY_MESSAGES = 8  # 질문 작성기에 넘길 최근 메시지 수 (사용자 + 도우미)


def _load_state(db: sqlite3.Connection, session_id: str) -> ChatState:
    row = db.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return ChatState.from_dict(json.loads(row["state"]), settings.default_category)


def _load_catalog(db: sqlite3.Connection, category_id: str, embed: Embedder | None = None) -> Catalog:
    # 요청마다 DB에서 읽는다(4,628개, 한 턴 약 70ms). 카탈로그를 다시 빌드해도 재시작이 필요 없다.
    spec = load_category(db, category_id)
    products = load_products(db, category_id) if spec else []
    if spec is None or not products:
        raise HTTPException(
            status_code=503,
            detail=f"'{category_id}' 카탈로그가 없습니다. scripts/build_catalog.py를 먼저 실행하세요.",
        )
    return build_catalog(spec, products, text=load_text_index(db, category_id), embed=embed)


def _recent_messages(db: sqlite3.Connection, session_id: str) -> list[dict[str, str]]:
    """질문 작성기에 넘길 최근 대화 (오래된 것부터)."""
    rows = db.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, HISTORY_MESSAGES),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# 모델 id(없으면 기본 모델) → 그 모델로 만든 파서·질문 작성기 (API 키가 없으면 None)
ParserFactory = Callable[[str | None], Parser | None]
WriterFactory = Callable[[str | None], QuestionWriter | None]


def get_parser() -> ParserFactory:
    """LLM 파서. API 키가 없으면 None이고, 대화 관리자는 보기 문자열 매칭만 쓴다."""
    def make(model: str | None) -> Parser | None:
        try:
            return LLMParser(chat_client(model, "parse"))
        except LLMNotConfiguredError:
            return None
    return make


def get_embedder() -> Embedder | None:
    """동률 정렬의 질의 임베딩. API 키가 없으면 None이고, 텍스트 관련도는 BM25만 쓴다."""
    try:
        return embedder_for(get_llm_client())
    except LLMNotConfiguredError:
        return None


def get_question_writer() -> WriterFactory:
    """질문 작성기. API 키가 없으면 None이고, 대화 관리자는 카테고리 정의의 고정 질문을 쓴다."""
    def make(model: str | None) -> QuestionWriter | None:
        try:
            return LLMQuestionWriter(chat_client(model, "question"))
        except LLMNotConfiguredError:
            return None
    return make


@router.post("", response_model=SessionCreated, status_code=201)
def create_session(db: sqlite3.Connection = Depends(get_db)) -> SessionCreated:
    session_id = uuid.uuid4().hex
    state = ChatState(category_id=settings.default_category)
    db.execute("INSERT INTO sessions (id, state) VALUES (?, ?)",
               (session_id, json.dumps(state.to_dict(), ensure_ascii=False)))
    return SessionCreated(session_id=session_id)


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str, db: sqlite3.Connection = Depends(get_db)) -> SessionDetail:
    state = _load_state(db, session_id)
    rows = db.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()
    question = None
    if state.pending is not None:
        spec = load_category(db, state.category_id)
        question = build_question(spec, state.pending.attribute, state.pending.text) if spec else None
    return SessionDetail(
        session_id=session_id,
        messages=[Message(role=r["role"], content=r["content"]) for r in rows],
        question=question,
        finished=state.finished,
    )


@router.post("/{session_id}/messages", response_model=ChatResponse)
def post_message(
    session_id: str,
    body: ChatRequest,
    db: sqlite3.Connection = Depends(get_db),
    make_parser: ParserFactory = Depends(get_parser),
    make_writer: WriterFactory = Depends(get_question_writer),
    embed: Embedder | None = Depends(get_embedder),
) -> ChatResponse:
    """@see docs/architecture.md#1-한눈에-보기"""
    if body.model is not None and body.model not in {m.id for m in chat_models()}:
        raise HTTPException(status_code=422, detail=f"고를 수 없는 모델: {body.model}")
    parser, writer = make_parser(body.model), make_writer(body.model)
    state = _load_state(db, session_id)
    catalog = _load_catalog(db, state.category_id, embed)
    answer = (body.answer.attribute, body.answer.value) if body.answer else None
    history = _recent_messages(db, session_id)
    try:
        result = handle_message(state, body.message, catalog, answer, parser, writer, history)
    except InvalidAnswer as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    result.debug.model = body.model or settings.openai_model

    db.executemany(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        [(session_id, "user", body.message), (session_id, "assistant", result.reply)],
    )
    db.execute(
        "UPDATE sessions SET state = ? WHERE id = ?",
        (json.dumps(result.state.to_dict(), ensure_ascii=False), session_id),
    )
    return ChatResponse(
        reply=result.reply, question=result.question, recommendations=result.recommendations,
        debug=result.debug, finished=result.state.finished,
    )


models_router = APIRouter(prefix="/api/models", tags=["models"])


@models_router.get("", response_model=ModelList)
def list_models() -> ModelList:
    """입력창의 모델 선택지. 기본값은 설정의 OPENAI_MODEL."""
    return ModelList(default=settings.openai_model,
                     options=[ModelChoice(id=m.id, input_price=m.input_price, output_price=m.output_price)
                              for m in chat_models()])
