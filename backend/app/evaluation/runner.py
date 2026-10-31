"""평가 실행: 시나리오 × 시스템마다 가상 사용자와 대화시키고, 정답 적중과 LLM 심판 점수를 남긴다.

결과는 SQLite(eval_runs, eval_episodes)에 저장해 Developer Panel의 평가 모드에서 본다.

@see docs/briefing.md#4-아키텍처와-코드-지도
"""

import json
import logging
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from app.catalog.repository import load_category, load_products
from app.catalog.search import CatalogSearch
from app.catalog.spec import CategorySpec
from app.config import settings
from app.db import connect
from app.evaluation.agents import simulated_user
from app.evaluation.judge import judge_episode
from app.evaluation.scenarios import Scenario, load_scenarios
from app.evaluation.systems import SYSTEMS, make_system
from app.llm import LLMClient, get_llm_client
from app.services.chat import Catalog, Parser, QuestionWriter, build_catalog
from app.services.parser import LLMParser
from app.services.questioner import LLMQuestionWriter
from app.services.relevance import embedder_for, load_text_index

logger = logging.getLogger(__name__)

MAX_TURNS = 8  # 시스템 응답 수 상한. 그 안에 추천하지 않으면 추천 없음으로 끝난다
WORKERS = 4
_db_lock = threading.Lock()


@dataclass
class EvalContext:
    spec: CategorySpec
    catalog: Catalog
    search: CatalogSearch
    client: LLMClient  # 가상 사용자, 심판, LLM 비교군
    parser: Parser
    writer: QuestionWriter


def load_context(conn: sqlite3.Connection, category_id: str) -> EvalContext:
    spec = load_category(conn, category_id)
    if spec is None:
        raise ValueError(f"카탈로그 없음: {category_id}")
    client = get_llm_client()
    writer_client = get_llm_client(settings.openai_question_model,
                                   settings.openai_question_reasoning_effort or "")
    catalog = build_catalog(spec, load_products(conn, category_id), text=load_text_index(conn, category_id),
                            embed=embedder_for(client))
    return EvalContext(spec, catalog,
                       CatalogSearch.from_db(conn, category_id), client,
                       LLMParser(client), LLMQuestionWriter(writer_client))


# ---------------------------------------------------------------- 지표

def attribute_match(scenario: Scenario, product_attrs: dict[str, str]) -> float | None:
    """1위 상품이 정답 상품의 속성과 얼마나 같은가 (정답의 알려진 속성 중 같은 비율)."""
    target = scenario.target_attributes
    if not target:
        return None
    return sum(product_attrs.get(k) == v for k, v in target.items()) / len(target)


def episode_metrics(scenario: Scenario, recommended: list[str], catalog: Catalog) -> dict:
    top1 = catalog.products.get(recommended[0]) if recommended else None
    return {
        "hit1": scenario.target_id in recommended[:1],
        "hit3": scenario.target_id in recommended[:3],
        "attribute_match": attribute_match(scenario, top1.attributes) if top1 else None,
    }


def summarize(results: list[dict]) -> dict[str, dict]:
    """시스템별 평균 지표."""
    out = {}
    for system in sorted({r["system"] for r in results}):
        rs = [r for r in results if r["system"] == system and not r.get("error")]

        def avg(values):
            values = [v for v in values if v is not None]
            return round(mean(values), 3) if values else None

        ranks = [r["target_rank"] for r in rs if r.get("target_rank")]
        no_text = [r["extra"]["target_rank_no_text"] for r in rs
                   if (r.get("extra") or {}).get("target_rank_no_text")]
        out[system] = {
            "episodes": len(rs),
            "errors": sum(1 for r in results if r["system"] == system and r.get("error")),
            "recommended": sum(1 for r in rs if r["recommended"]),
            "hit1": avg([float(r["hit1"]) for r in rs]),
            "hit3": avg([float(r["hit3"]) for r in rs]),
            "mrr": avg([1 / r for r in ranks]) if ranks else None,
            "median_rank": median(ranks) if ranks else None,
            # 동률 정렬에서 텍스트 관련도를 뺐을 때 (주 용도 → 평점만)
            "median_rank_no_text": median(no_text) if no_text else None,
            "hit3_no_text": avg([float(v <= 3) for v in no_text]) if no_text else None,
            "attribute_match": avg([r["attribute_match"] for r in rs]),
            "questions": avg([r["questions"] for r in rs]),
            "fit": avg([r["judge"]["fit"] for r in rs if r.get("judge")]),
            "naturalness": avg([r["judge"]["naturalness"] for r in rs if r.get("judge")]),
            "efficiency": avg([r["judge"]["efficiency"] for r in rs if r.get("judge")]),
            "seconds": avg([r["seconds"] for r in rs]),
        }
    return out


# ---------------------------------------------------------------- 에피소드

def run_episode(ctx: EvalContext, scenario: Scenario, system_name: str) -> dict:
    """가상 사용자와 시스템 한 쌍의 대화 한 번.

    @docid eval.run-episode
    @see docs/architecture.md#1-한눈에-보기
    @see docs/evaluation.md#3-에피소드와-지표
    """
    start = time.perf_counter()
    seed = int(scenario.id.rsplit("-", 1)[-1]) if scenario.id.rsplit("-", 1)[-1].isdigit() else 0
    system = make_system(system_name, ctx.catalog, parser=ctx.parser, writer=ctx.writer,
                         client=ctx.client, search=ctx.search, seed=seed)
    user = simulated_user(ctx.client, scenario, ctx.spec.name_for("en"))
    transcript: list[dict[str, str]] = []
    recommended: list[str] = []
    extra: dict = {}
    questions = 0
    message = scenario.opening
    for _ in range(MAX_TURNS):
        transcript.append({"role": "user", "content": message})
        turn = system.respond(message)
        transcript.append({"role": "assistant", "content": turn.reply})
        extra = {**extra, **{k: v for k, v in turn.extra.items() if k != "asked"}}
        if turn.recommended:
            recommended = turn.recommended
            break
        questions += 1
        message = user.reply(turn.reply)

    rank = system.target_rank(scenario.target_id) if hasattr(system, "target_rank") else None
    if hasattr(system, "target_rank"):
        extra["target_rank_no_text"] = system.target_rank(scenario.target_id, use_text=False)
    if system_name == "llm":
        extra["target_in_candidates"] = scenario.target_id in extra.get("candidate_ids", [])
        extra.pop("candidate_ids", None)
    products = [ctx.catalog.products[i] for i in recommended if i in ctx.catalog.products]
    judge = judge_episode(ctx.client, ctx.spec, scenario, transcript, products)
    return {
        "scenario_id": scenario.id, "system": system_name, "target_id": scenario.target_id,
        "recommended": recommended, "target_rank": rank, "questions": questions,
        **episode_metrics(scenario, recommended, ctx.catalog),
        "judge": judge.model_dump(), "seconds": round(time.perf_counter() - start, 1),
        "transcript": transcript, "extra": extra,
    }


def safe_episode(ctx: EvalContext, scenario: Scenario, system_name: str) -> dict:
    try:
        return run_episode(ctx, scenario, system_name)
    except Exception as e:  # 에피소드 하나가 실패해도 나머지는 계속한다
        logger.exception("episode failed: %s %s", scenario.id, system_name)
        return {"scenario_id": scenario.id, "system": system_name, "target_id": scenario.target_id,
                "recommended": [], "error": f"{type(e).__name__}: {e}"[:300]}


# ---------------------------------------------------------------- 실행 (DB 기록)

def create_run(conn: sqlite3.Connection, systems: list[str], scenarios: list[Scenario]) -> int:
    config = {"systems": systems, "scenario_ids": [s.id for s in scenarios]}
    cur = conn.execute("INSERT INTO eval_runs (status, config, total) VALUES ('running', ?, ?)",
                       (json.dumps(config), len(systems) * len(scenarios)))
    return int(cur.lastrowid)


def execute_run(db_path: Path, run_id: int, category_id: str, systems: list[str],
                scenarios: list[Scenario], workers: int = WORKERS) -> dict:
    """에피소드를 병렬로 돌리며 끝날 때마다 DB에 기록하고, 마지막에 요약을 남긴다.

    @docid eval.execute-run
    """
    try:
        with closing(connect(db_path)) as conn:
            ctx = load_context(conn, category_id)
        jobs = [(s, name) for s in scenarios for name in systems]
        results = []

        def one(job):
            result = safe_episode(ctx, *job)
            with _db_lock, closing(connect(db_path)) as conn:
                conn.execute("INSERT INTO eval_episodes (run_id, scenario_id, system, result) VALUES (?, ?, ?, ?)",
                             (run_id, result["scenario_id"], result["system"],
                              json.dumps(result, ensure_ascii=False)))
                conn.execute("UPDATE eval_runs SET done = done + 1 WHERE id = ?", (run_id,))
                conn.commit()
            return result

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, jobs))
        summary = summarize(results)
        with closing(connect(db_path)) as conn:
            conn.execute("UPDATE eval_runs SET status = 'done', summary = ? WHERE id = ?",
                         (json.dumps(summary), run_id))
            conn.commit()
        return summary
    except Exception as e:
        with closing(connect(db_path)) as conn:
            conn.execute("UPDATE eval_runs SET status = 'failed', error = ? WHERE id = ?",
                         (traceback.format_exc()[-2000:] or str(e), run_id))
            conn.commit()
        raise


def start_run(db_path: Path, category_id: str, systems: list[str], limit: int) -> int:
    """평가를 백그라운드 스레드로 시작하고 run id를 돌려준다."""
    unknown = set(systems) - set(SYSTEMS)
    if unknown or not systems:
        raise ValueError(f"시스템은 {SYSTEMS} 중에서 고른다: {sorted(unknown)}")
    scenarios = load_scenarios(category_id)[:limit]
    if not scenarios:
        raise ValueError("시나리오가 없다. scripts/build_scenarios.py를 먼저 실행한다")
    with closing(connect(db_path)) as conn:
        run_id = create_run(conn, systems, scenarios)
        conn.commit()
    _spawn(_run_quietly, db_path, run_id, category_id, systems, scenarios)
    return run_id


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _run_quietly(*args) -> None:
    try:
        execute_run(*args)
    except Exception:
        logger.exception("evaluation run failed")
