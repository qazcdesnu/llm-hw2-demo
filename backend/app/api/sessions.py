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
    Catalog, ChatState, InvalidAnswer, Parser, build_catalog, build_question, handle_message,
)
from app.services.parser import LLMParser

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _load_state(db: sqlite3.Connection, session_id: str) -> ChatState:
    """@see docs/architecture.md#3-대화-한-턴의-흐름"""
    row = db.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return ChatState.from_dict(json.loads(row["state"]), settings.default_category)


def _load_catalog(db: sqlite3.Connection, category_id: str) -> Catalog:
    # 상품 150개 규모라 요청마다 DB에서 읽는다. 카탈로그를 다시 빌드해도 재시작이 필요 없다.
    spec = load_category(db, category_id)
    products = load_products(db, category_id) if spec else []
    if spec is None or not products:
        raise HTTPException(
            status_code=503,
            detail=f"'{category_id}' 카탈로그가 없습니다. scripts/build_catalog.py를 먼저 실행하세요.",
        )
    return build_catalog(spec, products)


def get_parser() -> Parser | None:
    """LLM 파서. API 키가 없으면 None이고, 대화 관리자는 보기 문자열 매칭만 쓴다."""
    try:
        return LLMParser(get_llm_client())
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
        question = build_question(spec, state.pending) if spec else None
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
) -> ChatResponse:
    """@see docs/architecture.md#1-한눈에-보기"""
    state = _load_state(db, session_id)
    catalog = _load_catalog(db, state.category_id)
    answer = (body.answer.attribute, body.answer.value) if body.answer else None
    try:
        result = handle_message(state, body.message, catalog, answer, parser)
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
