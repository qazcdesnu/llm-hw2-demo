"""후보 상품에서 카탈로그용 상품을 비례 층화 추출한다.

@see docs/category-spec.md#파일-형식
"""

import math
import random
from collections import defaultdict

from app.catalog.extract import Extracted
from app.catalog.spec import SamplingSpec

# (원본 상품, 추출 속성)
Row = tuple[dict, dict[str, Extracted]]


def eligible(rows: list[Row], spec: SamplingSpec) -> list[Row]:
    return [r for r in rows if all(key in r[1] for key in spec.require_known)]


def allocate(sizes: dict[tuple, int], n: int) -> dict[tuple, int]:
    """층별 크기에 비례해 n개를 나눈다 (최대 잉여법). 층 크기를 넘지 않는다."""
    total = sum(sizes.values())
    quotas = {k: n * s / total for k, s in sizes.items()}
    alloc = {k: min(math.floor(q), sizes[k]) for k, q in quotas.items()}
    # 소수점 아래가 큰 층부터 하나씩 더 준다. 동률은 층 키 순서로 고정.
    order = sorted(sizes, key=lambda k: (-(quotas[k] - math.floor(quotas[k])), k))
    remaining = n - sum(alloc.values())
    while remaining > 0:
        progressed = False
        for k in order:
            if remaining == 0:
                break
            if alloc[k] < sizes[k]:
                alloc[k] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return alloc


def stratified_sample(rows: list[Row], spec: SamplingSpec) -> list[Row]:
    pool = eligible(rows, spec)
    if len(pool) <= spec.size:
        return pool

    strata: dict[tuple, list[Row]] = defaultdict(list)
    for row in pool:
        key = tuple(row[1][k].value if k in row[1] else "unknown" for k in spec.stratify_by)
        strata[key].append(row)

    rng = random.Random(spec.seed)
    alloc = allocate({k: len(v) for k, v in strata.items()}, spec.size)
    sample: list[Row] = []
    for key in sorted(strata):
        # 입력 순서와 무관하게 재현되도록 상품 id로 정렬한 뒤 뽑는다.
        members = sorted(strata[key], key=lambda r: r[0]["parent_asin"])
        sample.extend(rng.sample(members, alloc[key]))
    return sample
