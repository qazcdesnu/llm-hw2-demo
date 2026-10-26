from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import sessions
from app.config import settings
from app.db import init_db


def create_app(db_path: Path | None = None, frontend_dist: Path | None = None) -> FastAPI:
    """앱 생성. frontend_dist에 빌드된 프론트가 있으면 "/"에서 함께 서비스한다(단일 서버 배포).

    @see README.md#배포
    """
    db_path = db_path or settings.db_path
    frontend_dist = frontend_dist or settings.frontend_dist

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

    # API 라우트보다 뒤에 마운트해야 /api 요청이 정적 파일로 가지 않는다.
    if (frontend_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
