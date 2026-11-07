"""저장된 평가 run의 추천 목록으로 추천 집중도(동질화·인기도 편향)와 속성 일치를 계산한다.

새 대화를 돌리지 않는다(LLM 호출 없음). DB는 읽기 전용으로 연다.
run마다 따로 계산하고(--runs), 여러 run을 합쳐서도 계산한다(--pool: 같은 시나리오 반복의 일관성을 함께 본다).
결과는 run별 JSON과 지표별 CSV로 남긴다.

    uv run python scripts/analyze_diversity.py headphones --pool 12 13              # 현재 버전(같은 코드 2회)
    uv run python scripts/analyze_diversity.py headphones --runs 12 13 --pool 12 13 --out /tmp/div

@see docs/diversity.md#3-실행
"""

import argparse
import csv
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.repository import Product, load_category, load_products  # noqa: E402
from app.catalog.spec import CategorySpec  # noqa: E402
from app.config import BACKEND_DIR, settings  # noqa: E402
from app.evaluation import diversity as dv  # noqa: E402
from app.evaluation.scenarios import Scenario, load_scenarios  # noqa: E402

OUT_DIR = BACKEND_DIR / "evaluation" / "diversity"
TOP_N = 10
SYSTEMS = ["ig", "random", "llm"]
TARGET = "target"  # 실제 구매 상품 (사용자당 1개)

# CSV로 내보낼 지표 (JSON의 평탄화된 키). 순서가 CSV 파일 목록의 순서다
METRICS = [
    "unique_items", "slots", "distinct_ratio", "coverage", "gini_recommended", "effective_items",
    f"top{TOP_N}_share", "max_count", "popularity_mean", "popularity_median",
    "same_scenario_pairs", "same_scenario_overlap_pairs", "same_scenario_jaccard",
    "same_need_pairs", "same_need_overlap_pairs", "same_need_jaccard",
    "different_need_pairs", "different_need_overlap_pairs", "different_need_jaccard",
    "attr_top1", "attr_top3_mean", "attr_top3_max", "attr_p0", "attr_top1_lift",
    "stored_attribute_match", "rank_pct_mean", "rank_pct_median",
]


@dataclass
class Context:
    spec: CategorySpec
    catalog: list[Product]
    products: dict[str, Product]
    scenarios: dict[str, Scenario]
    targets: dict[str, dict[str, str]]  # 시나리오 → 지금 정의에 있는 정답 속성
    signature: dict[str, tuple]  # 시나리오 → 정답의 조건 속성 조합 ("필요")
    p0: dict[str, float | None]  # 시나리오 → 우연 기준선
    cell_size: dict[str, int]  # 시나리오 → 조건 속성 조합이 같은 카탈로그 상품 수


def _avg(values):
    values = [v for v in values if v is not None]
    return round(mean(values), 4) if values else None


def _med(values):
    values = [v for v in values if v is not None]
    return round(median(values), 4) if values else None


def _round(x, nd=4):
    return None if x is None else round(x, nd)


def load_context(conn: sqlite3.Connection, category_id: str) -> Context:
    """@see docs/diversity.md#3-실행"""
    spec = load_category(conn, category_id)
    if spec is None:
        sys.exit(f"{category_id}: DB에 카탈로그 없음")
    catalog = load_products(conn, category_id)
    scenarios = {s.id: s for s in load_scenarios(category_id)}
    constraint_keys = {a.key for a in spec.attributes if a.askable}  # 시스템이 묻고 거르는 속성 (brand 제외)
    # 시나리오에 남아 있어도 지금 정의에서 뺀 속성(예: use_case)은 비교하지 않는다 (runner.attribute_match와 같음)
    targets = {sid: {k: v for k, v in s.target_attributes.items() if spec.has_attribute(k)}
               for sid, s in scenarios.items()}
    signature = {sid: tuple(sorted((k, v) for k, v in t.items() if k in constraint_keys))
                 for sid, t in targets.items()}
    return Context(
        spec=spec, catalog=catalog, products={p.id: p for p in catalog}, scenarios=scenarios,
        targets=targets, signature=signature,
        p0={sid: dv.chance_match(t, catalog) for sid, t in targets.items()},
        cell_size={sid: sum(all(p.attributes.get(k) == v for k, v in sig) for p in catalog)
                   for sid, sig in signature.items()},
    )


def load_episodes(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute("SELECT result FROM eval_episodes WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [{**json.loads(r[0]), "run": run_id} for r in rows]


def concentration(lists: dv.Lists, catalog_size: int) -> dict:
    """@see docs/diversity.md#3-실행"""
    counts = dv.exposure(lists)
    slots = sum(len(dict.fromkeys(r)) for r in lists)
    return {
        "episodes": len(lists),
        "slots": slots,
        "unique_items": len(counts),
        "distinct_ratio": _round(dv.distinct_ratio(lists)),
        "coverage": _round(dv.coverage(lists, catalog_size), 5),
        "coverage_max": _round(slots / catalog_size, 5),
        "gini_recommended": _round(dv.gini(counts.values())),
        "gini_catalog": _round(dv.gini(list(counts.values()) + [0] * (catalog_size - len(counts)))),
        "effective_items": _round(dv.effective_items(counts), 2),
        f"top{TOP_N}_share": _round(dv.top_share(counts, TOP_N)),
        "max_count": max(counts.values(), default=0),
        "curve": dv.accumulation_curve(lists),
    }


def attribute_report(ctx: Context, episodes: list[dict]) -> dict:
    """시스템 하나의 속성 일치: 1위·Top-3, 우연 기준선, 속성별 같음/다름/확인 불가.

    @see docs/diversity.md#3-실행
    """
    top1, top3_mean, top3_max, lift1, p0s = [], [], [], [], []
    per_attr: dict[str, dict[str, dict[str, int]]] = {"top1": {}, "top3": {}}
    for ep in episodes:
        target = ctx.targets[ep["scenario_id"]]
        recs = [ctx.products[i] for i in ep["recommended"] if i in ctx.products]
        if not recs or not target:
            continue
        rates = [dv.match_rate(target, p.attributes) for p in recs]
        p0 = ctx.p0[ep["scenario_id"]]
        top1.append(rates[0])
        top3_mean.append(mean(rates))
        top3_max.append(max(rates))
        lift1.append(dv.lift(rates[0], p0))
        p0s.append(p0)
        for scope, ps in (("top1", recs[:1]), ("top3", recs)):
            for p in ps:
                for attr, status in dv.attribute_status(target, p.attributes).items():
                    cell = per_attr[scope].setdefault(attr, {dv.MATCH: 0, dv.MISMATCH: 0, dv.UNKNOWN: 0})
                    cell[status] += 1
    return {
        "top1": _avg(top1), "top3_mean": _avg(top3_mean), "top3_max": _avg(top3_max),
        "p0": _avg(p0s), "top1_lift": _avg(lift1),
        "per_attribute": {scope: dict(sorted(cells.items())) for scope, cells in per_attr.items()},
    }


def per_scenario(ctx: Context, episodes: list[dict], recs: dict[str, list[str]],
                 scenario_of: dict[str, str]) -> dict[str, dict]:
    """시스템 하나의 시나리오별 지표: 속성 일치·lift·정답 순위(에피소드 평균), 같은 시나리오 반복의 Jaccard,
    다른 시나리오의 에피소드와 겹친 수(필요가 같은 / 다른 시나리오).

    @see docs/diversity.md#3-실행
    """
    out = {}
    for sid in dict.fromkeys(e["scenario_id"] for e in episodes):
        eps = [e for e in episodes if e["scenario_id"] == sid]
        mine = [u for u in recs if scenario_of[u] == sid]
        others = [u for u in recs if scenario_of[u] != sid]
        top1 = [dv.match_rate(ctx.targets[sid], ctx.products[e["recommended"][0]].attributes)
                for e in eps if e["recommended"] and e["recommended"][0] in ctx.products]
        overlap = [(u, v) for u in mine for v in others if set(recs[u]) & set(recs[v])]
        out[sid] = {
            "episodes": len(eps),
            "constraint_cell_size": ctx.cell_size[sid],
            "attr_top1": _avg(top1),
            "attr_top1_lift": _avg([dv.lift(m, ctx.p0[sid]) for m in top1]),
            "rank_pct_mean": _avg([e.get("rank_pct") for e in eps]),
            "repeat_jaccard": _avg([dv.jaccard(recs[u], recs[v]) for i, u in enumerate(mine) for v in mine[i + 1:]]),
            "overlap_same_need": sum(ctx.signature[scenario_of[v]] == ctx.signature[sid] for _, v in overlap),
            "overlap_different_need": sum(ctx.signature[scenario_of[v]] != ctx.signature[sid] for _, v in overlap),
        }
    return out


def analyze(ctx: Context, episodes: list[dict], label: str) -> dict:
    """에피소드 묶음(run 하나 또는 여러 run)의 집중도·속성 일치. 사용자 = run:시나리오.

    @see docs/diversity.md#3-실행
    """
    episodes = [e for e in episodes if not e.get("error")]
    missing = sorted({e["scenario_id"] for e in episodes} - set(ctx.scenarios))
    if missing:
        raise ValueError(f"시나리오 파일에 없는 시나리오: {missing}")
    runs = sorted({e["run"] for e in episodes})
    pooled = len(runs) > 1
    user = lambda e: f"{e['run']}:{e['scenario_id']}"  # noqa: E731
    users = list(dict.fromkeys(user(e) for e in episodes))  # 실행 순서
    scenario_of = {user(e): e["scenario_id"] for e in episodes}
    signature = {u: ctx.signature[scenario_of[u]] for u in users}
    order = list(dict.fromkeys(scenario_of[u] for u in users))  # 서로 다른 시나리오

    systems = [s for s in SYSTEMS if any(e["system"] == s for e in episodes)]
    by_system = {s: {user(e): e["recommended"] for e in episodes if e["system"] == s} for s in systems}
    size = len(ctx.catalog)

    target_ids = [ctx.scenarios[sid].target_id for sid in order]
    out: dict = {
        "label": label, "runs": runs, "category": ctx.spec.id, "catalog_size": size,
        "users": users,
        "scenarios": {sid: {"target_id": ctx.scenarios[sid].target_id, "target_attributes": ctx.targets[sid],
                            "constraint_cell_size": ctx.cell_size[sid], "p0": _round(ctx.p0[sid])}
                      for sid in order},
        TARGET: {**concentration([[t] for t in target_ids], size),
                 "popularity_mean": _avg(dv.popularity_percentiles(target_ids, ctx.products)),
                 "popularity_median": _med(dv.popularity_percentiles(target_ids, ctx.products))},
        "systems": {},
    }
    for s in systems:
        lists = [by_system[s][u] for u in users if u in by_system[s]]
        eps = [e for e in episodes if e["system"] == s]
        pops = dv.popularity_percentiles([i for r in lists for i in r], ctx.products)
        recs = by_system[s]
        out["systems"][s] = {
            **concentration(lists, size),
            "popularity_mean": _avg(pops), "popularity_median": _med(pops),
            "pairwise": dv.pairwise_overlap(recs, {u: signature[u] for u in recs},
                                            {u: scenario_of[u] for u in recs} if pooled else None),
            "attribute_match": attribute_report(ctx, eps),
            "stored_attribute_match": _avg([e.get("attribute_match") for e in eps]),
            "rank_pct_mean": _avg([e.get("rank_pct") for e in eps]),
            "rank_pct_median": _med([e.get("rank_pct") for e in eps]),
            "per_scenario": per_scenario(ctx, eps, recs, scenario_of),
        }

    labeled = {f"{s}:{u}": by_system[s][u] for s in systems for u in users if u in by_system[s]}
    out["repeated_items"] = [
        {"item": item, "count": len(where), "scenarios": len({w.rsplit(":", 1)[1] for w in where}),
         "where": where, "title": ctx.products[item].title if item in ctx.products else None,
         "rating_number": ctx.products[item].rating_number if item in ctx.products else None,
         "popularity": _round(dv.popularity_percentiles([item], ctx.products)[0]) if item in ctx.products else None}
        for item, where in dv.repeated_items(labeled).items()
    ]
    out["cross_system"] = {
        f"{a}-{b}": {"same_episode_jaccard": _avg([dv.jaccard(by_system[a].get(u, []), by_system[b].get(u, []))
                                                   for u in users]),
                     "union_jaccard": _round(dv.jaccard(dv.exposure(list(by_system[a].values())),
                                                        dv.exposure(list(by_system[b].values()))))}
        for i, a in enumerate(systems) for b in systems[i + 1:]
    }
    return out


# ---------------------------------------------------------------- 내보내기

def flat_metrics(report: dict) -> dict[str, dict[str, object]]:
    """{시스템: {지표: 값}}. 실제 구매 상품(target)은 집중도·인기도만."""
    rows: dict[str, dict[str, object]] = {}
    for s, v in report["systems"].items():
        row = {k: v.get(k) for k in METRICS if k in v}
        for group, p in v["pairwise"].items():
            row[f"{group}_pairs"] = p["pairs"]
            row[f"{group}_overlap_pairs"] = p["overlapping_pairs"]
            row[f"{group}_jaccard"] = _round(p["mean_jaccard"])
        a = v["attribute_match"]
        row.update(attr_top1=a["top1"], attr_top3_mean=a["top3_mean"], attr_top3_max=a["top3_max"],
                   attr_p0=a["p0"], attr_top1_lift=a["top1_lift"])
        rows[s] = row
    rows[TARGET] = {k: report[TARGET].get(k) for k in METRICS if k in report[TARGET]}
    return rows


def write_csvs(reports: list[dict], out_dir: Path) -> list[Path]:
    """지표별 CSV(행 = run, 열 = 시스템)와 전체 long 형식, 속성별·반복 상품·시스템 간 겹침 CSV.

    @see docs/diversity.md#3-실행
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    columns = SYSTEMS + [TARGET]
    flats = [(r["label"], flat_metrics(r)) for r in reports]
    written = []

    path = out_dir / "metrics_long.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "system", "metric", "value"])
        for label, rows in flats:
            for s in columns:
                for m in METRICS:
                    if s in rows and rows[s].get(m) is not None:
                        w.writerow([label, s, m, rows[s][m]])
    written.append(path)

    # 한눈에 보는 표: 행 = 지표, 열 = 시스템 (묶음이 여럿이면 묶음:시스템)
    keys = [(label, s) for label, rows in flats for s in columns if s in rows]
    path = out_dir / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", *[s if len(flats) == 1 else f"{label}:{s}" for label, s in keys]])
        rows_of = dict(flats)
        for m in METRICS:
            values = [rows_of[label][s].get(m) for label, s in keys]
            if any(v is not None for v in values):
                w.writerow([m, *["" if v is None else v for v in values]])
    written.append(path)

    path = out_dir / "per_scenario.csv"
    fields = ["episodes", "constraint_cell_size", "attr_top1", "attr_top1_lift", "rank_pct_mean",
              "repeat_jaccard", "overlap_same_need", "overlap_different_need"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "system", "scenario", *fields])
        for r in reports:
            for s, v in r["systems"].items():
                for sid, row in v["per_scenario"].items():
                    w.writerow([r["label"], s, sid, *["" if row[k] is None else row[k] for k in fields]])
    written.append(path)

    for m in METRICS:
        if not any(rows.get(s, {}).get(m) is not None for _, rows in flats for s in columns):
            continue
        cols = [s for s in columns if any(rows.get(s, {}).get(m) is not None for _, rows in flats)]
        path = out_dir / f"{m}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["group", *cols])
            for label, rows in flats:
                w.writerow([label, *[rows.get(s, {}).get(m, "") if rows.get(s, {}).get(m) is not None else ""
                                     for s in cols]])
        written.append(path)

    path = out_dir / "per_attribute.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "system", "scope", "attribute", "match", "mismatch", "unknown"])
        for r in reports:
            for s, v in r["systems"].items():
                for scope, cells in v["attribute_match"]["per_attribute"].items():
                    for attr, c in cells.items():
                        w.writerow([r["label"], s, scope, attr, c[dv.MATCH], c[dv.MISMATCH], c[dv.UNKNOWN]])
    written.append(path)

    path = out_dir / "repeated_items.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "item", "count", "scenarios", "rating_number", "popularity", "title", "where"])
        for r in reports:
            for it in r["repeated_items"]:
                w.writerow([r["label"], it["item"], it["count"], it["scenarios"], it["rating_number"],
                            it["popularity"], it["title"], " ".join(it["where"])])
    written.append(path)

    path = out_dir / "cross_system.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "pair", "same_episode_jaccard", "union_jaccard"])
        for r in reports:
            for pair, v in r["cross_system"].items():
                w.writerow([r["label"], pair, v["same_episode_jaccard"], v["union_jaccard"]])
    written.append(path)
    return written


def print_report(reports: list[dict]) -> None:
    cols = ["distinct_ratio", "same_need_overlap_pairs", "different_need_overlap_pairs", "popularity_mean",
            "attr_top1", "attr_top1_lift", "rank_pct_mean"]
    print("group    system  " + "".join(c[:16].rjust(17) for c in cols))
    for r in reports:
        rows = flat_metrics(r)
        for s in SYSTEMS + [TARGET]:
            if s in rows:
                print(f"{r['label']:<9}{s:<8}" + "".join(str(rows[s].get(c, "-")).rjust(17) for c in cols))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category")
    parser.add_argument("--runs", type=int, nargs="+", default=[], help="run마다 따로 분석")
    parser.add_argument("--pool", type=int, nargs="+", action="append", default=[],
                        help="이 run들을 합쳐서도 분석 (반복 실행의 일관성 포함). 여러 번 줄 수 있다")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="결과 폴더 (JSON, csv/)")
    args = parser.parse_args()

    reports = []
    with closing(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)) as conn:
        ctx = load_context(conn, args.category)
        groups = [(f"run{r}", [r]) for r in args.runs]
        groups += [(f"pool{'-'.join(map(str, pool))}" if len(pool) <= 2 else f"pool{min(pool)}-{max(pool)}", pool)
                   for pool in args.pool]
        for label, runs in groups:
            episodes = [e for r in runs for e in load_episodes(conn, r)]
            if not episodes:
                print(f"{label}: 에피소드 없음, 건너뜀")
                continue
            try:
                reports.append(analyze(ctx, episodes, label))
            except ValueError as e:
                print(f"{label}: {e}, 건너뜀")

    if not reports:
        sys.exit("분석할 묶음 없음 (--runs 또는 --pool)")
    # 이전 분석 결과는 지우고 이번 결과로 교체한다 (다른 묶음의 파일이 섞여 남지 않게)
    args.out.mkdir(parents=True, exist_ok=True)
    for old in [*args.out.glob("*.json"), *(args.out / "csv").glob("*.csv")]:
        old.unlink()
    for r in reports:
        (args.out / f"{r['label']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    written = write_csvs(reports, args.out / "csv")
    print_report(reports)
    print(f"\n→ {args.out}/<group>.json, CSV {len(written)}개 ({args.out / 'csv'})")


if __name__ == "__main__":
    main()
