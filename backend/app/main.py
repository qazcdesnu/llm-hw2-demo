from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api import sessions
from app.config import settings
from app.db import init_db


def create_app(db_path: Path | None = None) -> FastAPI:
    db_path = db_path or settings.db_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(db_path)
        yield

    app = FastAPI(title="RecommandShopping", lifespan=lifespan)
    app.state.db_path = db_path
    app.include_router(sessions.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
