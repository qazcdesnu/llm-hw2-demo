"""평가 API: 시나리오 목록, 매뉴얼 평가(역할극) 채점, 자동 평가 실행·결과 조회.

@see docs/evaluation.md#4-실행
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.sessions import _load_catalog, _load_state
from app.config import settings
from app.db import get_db
from app.evaluation.judge import describe_product
from app.evaluation.runner import episode_metrics, start_run
from app.evaluation.scenarios import load_scenarios
from app.evaluation.systems import SYSTEMS, target_rank
from app.llm import LLMNotConfiguredError

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


def _scenario(scenario_id: str):
    scenario = next((s for s in load_scenarios(settings.default_category) if s.id == scenario_id), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


@router.get("/scenarios")
def list_scenarios() -> list[dict]:
    """역할극용 시나리오. 정답 상품은 채점 전까지 보여주지 않는다."""
    return [{"id": s.id, "brief": s.brief, "persona": s.persona, "opening": s.opening,
             "query": s.query, "review": s.review}
            for s in load_scenarios(settings.default_category)]


class ManualScore(BaseModel):
    session_id: str
    scenario_id: str
    naturalness: int = Field(ge=1, le=5)
    satisfaction: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


@router.post("/manual")
def score_manual(body: ManualScore, db: sqlite3.Connection = Depends(get_db)) -> dict:
    """역할극 세션을 채점한다: 정답 적중·순위는 자동, 자연스러움·만족도는 사람이 매긴 점수."""
    scenario = _scenario(body.scenario_id)
    state = _load_state(db, body.session_id)
    if not state.finished:
        raise HTTPException(status_code=409, detail="아직 추천을 받지 않은 대화다")
    catalog = _load_catalog(db, state.category_id)
    recommended = list(state.last_recommended)
    target = catalog.products.get(scenario.target_id)
    result = {
        "scenario_id": scenario.id, "session_id": body.session_id,
        "target": {"id": scenario.target_id, "title": target.title if target else None,
                   "price": target.price if target else None,
                   "image_url": target.image_url if target else None,
                   "description": describe_product(catalog.spec, target) if target else None},
        "recommended": [{"id": i, "title": catalog.products[i].title} for i in recommended
                        if i in catalog.products],
        "target_rank": target_rank(catalog, state, scenario.target_id),
        **episode_metrics(scenario, recommended, catalog),
        "naturalness": body.naturalness, "satisfaction": body.satisfaction, "comment": body.comment,
        "turns": state.turn,
    }
    db.execute("INSERT INTO eval_manual (session_id, scenario_id, result) VALUES (?, ?, ?)",
               (body.session_id, scenario.id, json.dumps(result, ensure_ascii=False)))
    return result


@router.get("/manual")
def list_manual(db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    rows = db.execute("SELECT id, created_at, result FROM eval_manual ORDER BY id DESC").fetchall()
    return [{"id": r["id"], "created_at": r["created_at"], **json.loads(r["result"])} for r in rows]


class RunRequest(BaseModel):
    systems: list[str] = Field(default_factory=lambda: list(SYSTEMS))
    limit: int = Field(default=10, ge=1, le=200)


@router.post("/runs", status_code=202)
def create_run(body: RunRequest, request: Request) -> dict:
    """자동 평가를 백그라운드로 시작한다 (실제 LLM 호출, 비용 발생)."""
    try:
        run_id = start_run(request.app.state.db_path, settings.default_category, body.systems, body.limit)
    except LLMNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"run_id": run_id}


def _run_row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "created_at": r["created_at"], "status": r["status"],
            "config": json.loads(r["config"]), "done": r["done"], "total": r["total"],
            "summary": json.loads(r["summary"]) if r["summary"] else None, "error": r["error"]}


@router.get("/runs")
def list_runs(db: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    return [_run_row(r) for r in db.execute("SELECT * FROM eval_runs ORDER BY id DESC LIMIT 20")]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict:
    row = db.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    episodes = [json.loads(r["result"]) for r in db.execute(
        "SELECT result FROM eval_episodes WHERE run_id = ? ORDER BY scenario_id, system", (run_id,))]
    return {**_run_row(row), "episodes": episodes}
