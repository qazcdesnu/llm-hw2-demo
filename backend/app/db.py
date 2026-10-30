"""SQLite 연결과 스키마.

@see docs/architecture.md#5-데이터-모델-sqlite
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from fastapi import Request

# @see docs/architecture.md#5-데이터-모델-sqlite
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

-- 카테고리 정의 스냅샷. 원본은 categories/<id>.yaml이고, 카탈로그를 빌드할 때 함께 저장해
-- DB 안의 상품 속성과 정의가 항상 같은 버전이 되게 한다.
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    category_id TEXT NOT NULL REFERENCES categories(id),
    title TEXT NOT NULL,
    store TEXT,
    price REAL,
    average_rating REAL,
    rating_number INTEGER,
    image_url TEXT,
    features TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '[]',
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);

-- 속성 값은 카테고리와 무관한 key-value로 저장한다. 행이 없으면 unknown.
CREATE TABLE IF NOT EXISTS product_attributes (
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    attribute TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    evidence TEXT,
    PRIMARY KEY (product_id, attribute)
);
"""


EVAL_SCHEMA = """
-- 평가 (docs/evaluation.md). 결과 본문은 JSON.
CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL,            -- running / done / failed
    config TEXT NOT NULL,            -- {systems, limit, scenario_ids}
    done INTEGER NOT NULL DEFAULT 0, -- 끝난 에피소드 수
    total INTEGER NOT NULL DEFAULT 0,
    summary TEXT,                    -- 시스템별 지표
    error TEXT
);

CREATE TABLE IF NOT EXISTS eval_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    scenario_id TEXT NOT NULL,
    system TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_episodes_run ON eval_episodes(run_id);

CREATE TABLE IF NOT EXISTS eval_manual (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    session_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    result TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    # FastAPI는 sync 의존성과 엔드포인트를 서로 다른 스레드에서 실행할 수 있다.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.executescript(EVAL_SCHEMA)


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
