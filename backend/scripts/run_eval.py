"""자동 평가를 명령줄에서 실행한다 (실제 LLM 호출). 결과는 SQLite에 저장되어 평가 모드에서도 보인다.

    uv run python scripts/run_eval.py headphones --limit 10                 # ig, random, llm
    uv run python scripts/run_eval.py headphones --limit 3 --systems ig llm
"""

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.evaluation.runner import create_run, execute_run  # noqa: E402
from app.evaluation.scenarios import load_scenarios  # noqa: E402
from app.evaluation.systems import SYSTEMS  # noqa: E402

COLUMNS = ["episodes", "errors", "hit1", "hit3", "mrr", "median_rank", "attribute_match", "questions",
           "fit", "naturalness", "efficiency", "seconds"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--systems", nargs="+", default=list(SYSTEMS), choices=SYSTEMS)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    scenarios = load_scenarios(args.category)[: args.limit]
    if not scenarios:
        sys.exit("시나리오 없음. scripts/build_scenarios.py를 먼저 실행한다")
    init_db(settings.db_path)
    with closing(connect(settings.db_path)) as conn:
        run_id = create_run(conn, args.systems, scenarios)
        conn.commit()
    print(f"run {run_id}: 시나리오 {len(scenarios)}개 × {args.systems}")
    summary = execute_run(settings.db_path, run_id, args.category, args.systems, scenarios, args.workers)

    print(f"\n{'system':8}" + "".join(f"{c:>16}" for c in COLUMNS))
    for system, row in summary.items():
        print(f"{system:8}" + "".join(f"{json.dumps(row.get(c)):>16}" for c in COLUMNS))


if __name__ == "__main__":
    main()
