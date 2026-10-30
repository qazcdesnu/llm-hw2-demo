"""헤드폰 데이터의 속성 확보 가능성을 분석한다.

- 기본 통계: 상품 수, 세부 카테고리, 가격·리뷰 수 분포, 중복
- details 키별 커버리지와 값 분포 (구조화된 속성 후보)
- 텍스트(title/features/description/details) 키워드로 본 속성 증거 비율
전체 집합과 "후보 집합"(가격 있음, 리뷰 수 MIN_RATINGS 이상)에 대해 각각 계산한다.

    uv run python scripts/analyze_headphones.py

@see docs/data-analysis.md#헤드폰-데이터-분석-2026-10-26
"""

import json
import re
import statistics
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SRC = DATA_DIR / "interim" / "headphones.jsonl"
OUT = DATA_DIR / "interim" / "analysis.json"

MIN_RATINGS = 100
TOP_DETAIL_KEYS = 40
TOP_VALUES = 12

# 속성 증거 키워드. 한 상품이 여러 값에 걸릴 수 있다(예: 무선 헤드폰의 3.5mm 보조 단자).
KEYWORDS = {
    "conn:wireless": r"\bwireless\b|bluetooth|\btws\b",
    "conn:wired": r"\bwired\b|3\.5 ?mm|\baux\b",
    "anc:active": r"active noise|\banc\b|hybrid noise",
    "anc:any_noise_cancel": r"noise[- ]?cancel",
    "form:over_ear": r"over[- ]the[- ]ear|over[- ]ear|around[- ]the[- ]ear|circumaural",
    "form:on_ear": r"on[- ]ear\b|supra[- ]aural",
    "form:in_ear": r"in[- ]ear|ear ?buds?\b|earphones?",
    "form:open_ear": r"bone conduction|open[- ]ear|air conduction",
    "form:ear_hook": r"ear ?hooks?",
    "mic": r"\bmic\b|microphone",
    "water": r"ipx ?\d|\bip\d\d\b|waterproof|water[- ]resistan|sweat[- ]?proof",
    "use:sports": r"\bsports?\b|running|workout|\bgym\b",
    "use:gaming": r"gaming|gamer",
    "use:kids": r"\bkids?\b|children|volume[- ]limit",
    "battery_hours": r"\d+\s*(?:hours?|hrs?)\b",
    "foldable": r"foldable|folding",
}
PATTERNS = {k: re.compile(v) for k, v in KEYWORDS.items()}


def item_text(item: dict) -> str:
    parts = [item.get("title") or ""]
    parts += item.get("features") or []
    parts += item.get("description") or []
    parts += [f"{k}: {v}" for k, v in (item.get("details") or {}).items()]
    return " ".join(str(p) for p in parts).lower()


def quantiles(values: list[float]) -> dict:
    if len(values) < 2:
        return {}
    qs = statistics.quantiles(values, n=10)
    return {"min": min(values), "p10": qs[0], "p25": statistics.quantiles(values, n=4)[0],
            "median": statistics.median(values), "p75": statistics.quantiles(values, n=4)[2],
            "p90": qs[-1], "max": max(values)}


def analyze(items: list[dict]) -> dict:
    n = len(items)
    pct = lambda c: round(100 * c / n, 1) if n else 0.0  # noqa: E731

    leaves = Counter(" > ".join(i["categories"][3:]) or "(none)" for i in items)
    prices = [i["price"] for i in items if isinstance(i.get("price"), (int, float))]
    ratings = [i.get("rating_number") or 0 for i in items]
    titles = Counter(re.sub(r"\W+", " ", (i.get("title") or "").lower()).strip() for i in items)

    key_counts: Counter[str] = Counter()
    key_values: dict[str, Counter[str]] = {}
    for i in items:
        for k, v in (i.get("details") or {}).items():
            key_counts[k] += 1
            key_values.setdefault(k, Counter())[str(v)[:60]] += 1

    kw_counts: Counter[str] = Counter()
    combos: Counter[tuple] = Counter()
    for i in items:
        text = item_text(i)
        hits = {k for k, p in PATTERNS.items() if p.search(text)}
        kw_counts.update(hits)
        conn = "wireless" if "conn:wireless" in hits else ("wired" if "conn:wired" in hits else "?")
        form = next((f.split(":")[1] for f in ("form:over_ear", "form:on_ear", "form:open_ear",
                                               "form:in_ear") if f in hits), "?")
        anc = "anc" if "anc:active" in hits else "-"
        combos[(conn, form, anc)] += 1

    return {
        "count": n,
        "leaf_categories": leaves.most_common(),
        "price": {"present_pct": pct(len(prices)), **quantiles(prices)},
        "rating_number": quantiles(ratings),
        "nonempty_pct": {
            f: pct(sum(1 for i in items if i.get(f))) for f in ("features", "description", "details", "images", "store")
        },
        "duplicate_titles": sum(c - 1 for c in titles.values() if c > 1),
        "detail_keys": [
            {"key": k, "pct": pct(c), "top_values": key_values[k].most_common(TOP_VALUES)}
            for k, c in key_counts.most_common(TOP_DETAIL_KEYS)
        ],
        "keyword_pct": {k: pct(kw_counts[k]) for k in KEYWORDS},
        "naive_combo_counts": [[list(k), c] for k, c in combos.most_common(20)],
    }


def print_summary(name: str, r: dict) -> None:
    print(f"\n===== {name}: {r['count']:,} items =====")
    print("price:", r["price"])
    print("rating_number:", r["rating_number"])
    print("nonempty %:", r["nonempty_pct"], "| duplicate titles:", r["duplicate_titles"])
    print("\n-- leaf categories")
    for leaf, c in r["leaf_categories"][:15]:
        print(f"{c:8,}  {leaf}")
    print("\n-- keyword evidence %")
    for k, v in r["keyword_pct"].items():
        print(f"{v:6.1f}  {k}")
    print("\n-- detail keys (coverage %, top values)")
    for d in r["detail_keys"]:
        vals = ", ".join(f"{v}({c})" for v, c in d["top_values"][:6])
        print(f"{d['pct']:6.1f}  {d['key']}: {vals}")
    print("\n-- naive (connection, form, anc) combos")
    for combo, c in r["naive_combo_counts"]:
        print(f"{c:8,}  {combo}")


def main() -> None:
    with SRC.open(encoding="utf-8") as f:
        items = [json.loads(line) for line in f]
    candidates = [
        i for i in items
        if isinstance(i.get("price"), (int, float)) and (i.get("rating_number") or 0) >= MIN_RATINGS
    ]
    result = {"all": analyze(items), f"candidates_price_and_ratings>={MIN_RATINGS}": analyze(candidates)}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, r in result.items():
        print_summary(name, r)
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
