"""@see docs/architecture.md#1-한눈에-보기"""

import json
import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.catalog.repository import load_category, load_products
from app.config import settings
from app.db import get_db
from app.llm import LLMNotConfiguredError, get_llm_client
from app.schemas import ChatRequest, ChatResponse, Message, SessionCreated, SessionDetail
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


def get_parser() -> Parser | None:
    """LLM 파서. API 키가 없으면 None이고, 대화 관리자는 보기 문자열 매칭만 쓴다."""
    try:
        return LLMParser(get_llm_client())
    except LLMNotConfiguredError:
        return None


def get_embedder() -> Embedder | None:
    """동률 정렬의 질의 임베딩. API 키가 없으면 None이고, 텍스트 관련도는 BM25만 쓴다."""
    try:
        return embedder_for(get_llm_client())
    except LLMNotConfiguredError:
        return None


def get_question_writer() -> QuestionWriter | None:
    """질문 작성기. API 키가 없으면 None이고, 대화 관리자는 카테고리 정의의 고정 질문을 쓴다."""
    try:
        return LLMQuestionWriter(get_llm_client(settings.openai_question_model,
                                                settings.openai_question_reasoning_effort or ""))
    except LLMNotConfiguredError:
        return None


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
    parser: Parser | None = Depends(get_parser),
    writer: QuestionWriter | None = Depends(get_question_writer),
    embed: Embedder | None = Depends(get_embedder),
) -> ChatResponse:
    """@see docs/architecture.md#1-한눈에-보기"""
    state = _load_state(db, session_id)
    catalog = _load_catalog(db, state.category_id, embed)
    answer = (body.answer.attribute, body.answer.value) if body.answer else None
    history = _recent_messages(db, session_id)
    try:
        result = handle_message(state, body.message, catalog, answer, parser, writer, history)
    except InvalidAnswer as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

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
