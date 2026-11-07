"""추천 집중도와 속성 일치 분석: 저장된 추천 목록(에피소드별 Top-k)만으로 계산한다.

서로 다른 사용자에게 같은 상품이 얼마나 반복 추천되는가(aggregate diversity, 동질화, 인기도 편향)와
추천 상품이 정답 상품의 속성과 얼마나 같은가(우연 기준선 보정 포함)를 잰다. LLM은 부르지 않는다.

@see docs/diversity.md#2-지표-정의
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from itertools import combinations
from statistics import mean

from app.catalog.repository import Product

Lists = list[list[str]]  # 에피소드별 추천 상품 ID (순위순)


# ---------------------------------------------------------------- 집중도

def exposure(lists: Lists) -> Counter:
    """상품별 추천 횟수 (한 에피소드 안의 중복은 한 번으로).

    @see docs/diversity.md#2-지표-정의
    """
    return Counter(item for rec in lists for item in dict.fromkeys(rec))


def coverage(lists: Lists, catalog_size: int) -> float:
    """추천된 서로 다른 상품 수 / 카탈로그 크기. 상한은 k·n / catalog_size.

    @see docs/diversity.md#2-지표-정의
    """
    return len(exposure(lists)) / catalog_size if catalog_size else 0.0


def distinct_ratio(lists: Lists) -> float | None:
    """서로 다른 상품 수 / 추천 칸 수. 1이면 겹치는 추천이 없다.

    @see docs/diversity.md#2-지표-정의
    """
    slots = sum(len(dict.fromkeys(rec)) for rec in lists)
    return len(exposure(lists)) / slots if slots else None


def gini(counts: Iterable[int]) -> float | None:
    """노출 횟수의 Gini 계수. 0 = 모든 상품이 같은 횟수, 1에 가까울수록 소수에 쏠림.

    0인 상품(추천되지 않은 카탈로그 상품)을 넣으면 카탈로그 전체 기준이 된다.

    @see docs/diversity.md#2-지표-정의
    """
    xs = sorted(counts)
    n, total = len(xs), sum(xs)
    if n == 0 or total == 0:
        return None
    weighted = sum((i + 1) * x for i, x in enumerate(xs))
    return (2 * weighted) / (n * total) - (n + 1) / n


def effective_items(counter: Counter) -> float | None:
    """노출 분포의 exp(Shannon entropy): "사실상 몇 개를 고르게 돌려 쓰는가".

    @see docs/diversity.md#2-지표-정의
    """
    total = sum(counter.values())
    if not total:
        return None
    return math.exp(-sum(c / total * math.log(c / total) for c in counter.values()))


def top_share(counter: Counter, n: int = 10) -> float | None:
    """가장 많이 추천된 n개가 차지하는 추천 칸 비율.

    @see docs/diversity.md#2-지표-정의
    """
    total = sum(counter.values())
    return sum(c for _, c in counter.most_common(n)) / total if total else None


def accumulation_curve(lists: Lists) -> list[int]:
    """에피소드를 하나씩 더할 때까지의 서로 다른 상품 수.

    @see docs/diversity.md#2-지표-정의
    """
    seen: set[str] = set()
    curve = []
    for rec in lists:
        seen.update(rec)
        curve.append(len(seen))
    return curve


def repeated_items(labeled: Mapping[str, list[str]], min_count: int = 2) -> dict[str, list[str]]:
    """둘 이상의 에피소드에 나온 상품 → 그 에피소드 라벨들. labeled는 {에피소드 라벨: 추천 목록}.

    @see docs/diversity.md#2-지표-정의
    """
    where: dict[str, list[str]] = {}
    for label, rec in labeled.items():
        for item in dict.fromkeys(rec):
            where.setdefault(item, []).append(label)
    hits = {item: labels for item, labels in where.items() if len(labels) >= min_count}
    return dict(sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def pairwise_overlap(by_user: Mapping[str, list[str]], signature: Mapping[str, object],
                     scenario: Mapping[str, str] | None = None) -> dict[str, dict]:
    """에피소드 쌍의 추천 겹침을 같은 시나리오(반복 실행) / 필요가 같은 쌍 / 필요가 다른 쌍으로 나눈다.

    같은 시나리오끼리 겹치는 것은 일관성이고, 필요가 같은 사용자끼리 겹치는 것은 정당하다.
    필요가 다른데 겹치면 동질화(echo)로 본다.
    signature는 에피소드 → 비교용 키(예: 정답 상품의 조건 속성 조합), scenario는 에피소드 → 시나리오 ID
    (여러 run을 합칠 때만. 없으면 에피소드마다 다른 사용자로 본다).

    @see docs/diversity.md#2-지표-정의
    """
    groups: dict[str, list[float]] = {"same_scenario": [], "same_need": [], "different_need": []}
    for u, v in combinations(sorted(by_user), 2):
        if scenario is not None and scenario[u] == scenario[v]:
            group = "same_scenario"
        else:
            group = "same_need" if signature[u] == signature[v] else "different_need"
        groups[group].append(jaccard(by_user[u], by_user[v]))
    if scenario is None:
        del groups["same_scenario"]
    return {g: {"pairs": len(js), "overlapping_pairs": sum(j > 0 for j in js),
                "mean_jaccard": mean(js) if js else None}
            for g, js in groups.items()}


def popularity_percentiles(ids: Iterable[str], products: Mapping[str, Product]) -> list[float]:
    """상품별 리뷰 수의 카탈로그 백분위 (0~1, 리뷰 수가 그 이하인 상품의 비율). 1에 가까울수록 인기 상품.

    @see docs/diversity.md#2-지표-정의
    """
    counts = sorted(p.rating_number or 0 for p in products.values())
    n = len(counts)
    out = []
    for i in ids:
        if i in products:
            x = products[i].rating_number or 0
            out.append(_bisect_right(counts, x) / n)
    return out


def _bisect_right(xs: list[int], x: int) -> int:
    lo, hi = 0, len(xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


# ---------------------------------------------------------------- 속성 일치

MATCH, MISMATCH, UNKNOWN = "match", "mismatch", "unknown"


def attribute_status(target: Mapping[str, str], attrs: Mapping[str, str]) -> dict[str, str]:
    """정답의 알려진 속성마다 추천 상품이 같음 / 다름 / 확인 불가(상품 값이 unknown).

    @see docs/diversity.md#2-지표-정의
    """
    return {k: (UNKNOWN if k not in attrs else MATCH if attrs[k] == v else MISMATCH)
            for k, v in target.items()}


def match_rate(target: Mapping[str, str], attrs: Mapping[str, str],
               keys: Iterable[str] | None = None) -> float | None:
    """정답의 알려진 속성 중 같은 비율. unknown은 다름으로 센다
    (runner.attribute_match와 같은 정의). keys를 주면 그 속성만 본다.

    @see docs/diversity.md#2-지표-정의
    """
    keys = set(target) if keys is None else set(keys) & set(target)
    if not keys:
        return None
    return sum(attrs.get(k) == target[k] for k in keys) / len(keys)


def chance_match(target: Mapping[str, str], products: Iterable[Product],
                 keys: Iterable[str] | None = None) -> float | None:
    """우연 기준선 p₀: 카탈로그에서 아무 상품이나 골랐을 때의 기대 일치율 (전체 평균, 정확히 계산).

    @see docs/diversity.md#2-지표-정의
    """
    rates = [match_rate(target, p.attributes, keys) for p in products]
    rates = [r for r in rates if r is not None]
    return mean(rates) if rates else None


def lift(rate: float | None, p0: float | None) -> float | None:
    """우연 보정 일치: (일치 − p₀) / (1 − p₀). 0 = 무작위 수준, 1 = 모두 일치.

    @see docs/diversity.md#2-지표-정의
    """
    if rate is None or p0 is None or p0 >= 1:
        return None
    return (rate - p0) / (1 - p0)
