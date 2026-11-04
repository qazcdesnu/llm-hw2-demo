"""평가 실행 기록(SQLite eval_runs·eval_episodes)을 JSON으로 내보낸다 (git에 올려 공유·보관하려고).

    uv run python scripts/export_eval_runs.py 3 5 7 8 9 10 11 12 13

DB(app.db)는 git에서 제외되어 있어, 대화 기록과 지표는 내보내야 팀이 볼 수 있다.
결과: evaluation/runs/run-<id>.json (실행 설정, 시스템별 요약, 에피소드별 결과·대화 기록·심판 코멘트)
시각(created_at)은 DB에 저장된 그대로 UTC다.

@see README.md#평가-실행-기록-run별-코드-변경
"""

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import connect  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "evaluation" / "runs"


def export_run(conn, run_id: int) -> dict:
    row = conn.execute("SELECT id, created_at, status, config, summary FROM eval_runs WHERE id = ?",
                       (run_id,)).fetchone()
    if row is None:
        raise SystemExit(f"run {run_id} 없음")
    episodes = [json.loads(r[0]) for r in conn.execute(
        "SELECT result FROM eval_episodes WHERE run_id = ? ORDER BY scenario_id, system", (run_id,))]
    return {"id": row[0], "created_at_utc": row[1], "status": row[2], "config": json.loads(row[3]),
            "summary": json.loads(row[4]) if row[4] else None, "episodes": episodes}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+", type=int)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with closing(connect(settings.db_path)) as conn:
        for run_id in args.run_ids:
            path = OUT_DIR / f"run-{run_id:02d}.json"
            data = export_run(conn, run_id)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"run {run_id}: 에피소드 {len(data['episodes'])}개 -> {path}")


if __name__ == "__main__":
    main()
