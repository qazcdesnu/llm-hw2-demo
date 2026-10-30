"""실제 카탈로그로 IG 엔진을 돌려 본다 (자연어 없이 구조화된 답만 사용).

    # 목표 상품을 정하고, 그 상품의 속성대로 답하는 사용자로 대화
    uv run python scripts/ig_demo.py headphones --target B0BHR5VQC9
    # 답을 직접 지정 (지정하지 않은 속성은 '상관없음')
    uv run python scripts/ig_demo.py headphones --answers connection=wireless,anc=yes
    # 직접 입력
    uv run python scripts/ig_demo.py headphones --interactive
    # 질문 정책 비교용
    uv run python scripts/ig_demo.py headphones --target B0BHR5VQC9 --policy random --seed 1

@see docs/ig-engine.md#5-실행해-보기
"""

import argparse
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.repository import load_category, load_products  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.adapters import from_catalog  # noqa: E402
from app.core.engine import Ask, EngineConfig, IGEngine  # noqa: E402
from app.core.model import DONT_CARE, PreferenceState  # noqa: E402
from app.core.question_selector import (  # noqa: E402
    FixedOrderPolicy, InformationGainPolicy, RandomPolicy,
)
from app.db import connect  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--target", help="이 상품의 속성대로 답한다")
    group.add_argument("--answers", help="속성=값,속성=값 (나머지는 상관없음)")
    group.add_argument("--interactive", action="store_true")
    parser.add_argument("--policy", choices=["ig", "random", "fixed"], default="ig")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()

    with closing(connect(args.db)) as conn:
        spec = load_category(conn, args.category)
        products = load_products(conn, args.category)
    if spec is None:
        sys.exit(f"{args.category}: DB에 없음. scripts/build_catalog.py를 먼저 실행")
    attributes, items = from_catalog(spec, products)
    titles = {p.id: p.title for p in products}

    policy = {
        "ig": InformationGainPolicy(),
        "random": RandomPolicy(args.seed),
        "fixed": FixedOrderPolicy([a.key for a in spec.attributes]),
    }[args.policy]
    engine = IGEngine(items, attributes, EngineConfig(), policy)

    if args.target:
        target = next(i for i in items if i.id == args.target)
        answers = target.attributes
        print(f"target: {target.id} {titles[target.id][:70]}\n  {target.attributes}")
    elif args.answers:
        answers = dict(pair.split("=", 1) for pair in args.answers.split(","))
    else:
        answers = None

    state = PreferenceState()
    print(f"{len(items)} items, policy={policy.name}\n")
    while True:
        decision = engine.step(state)
        action = decision.action
        if not isinstance(action, Ask):
            break
        print(f"[turn {len(state.asked) + 1}] entropy {decision.entropy:.2f} bits")
        for c in action.candidates:
            mark = "→" if c.attribute == action.attribute else " "
            print(f"   {mark} {c.attribute:16} IG {c.expected_ig:.3f}")
        attr = spec.attribute(action.attribute)
        if answers is None:
            options = ", ".join(v.value for v in attr.values)
            value = input(f"   Q. {attr.question} ({options}, 엔터=상관없음) > ").strip() or DONT_CARE
        else:
            value = answers.get(action.attribute, DONT_CARE)
            print(f"   Q. {attr.question}\n   A. {value}")
        state.mark_asked(action.attribute)
        state.answer(action.attribute, value)

    print(f"\n추천 (종료 이유: {action.reason}, entropy {decision.entropy:.2f} bits)")
    for rank, r in enumerate(action.items, 1):
        hit = " ← target" if args.target and r.item.id == args.target else ""
        print(f"  {rank}. {r.probability:.3f}  {titles[r.item.id][:60]}{hit}")
        print(f"       맞음 {r.matched}  확인 불가 {r.unknown}  어긋남 {r.violated}")
    for lim in action.limitations:
        print(f"  한계: {lim.code} {lim.detail}")


if __name__ == "__main__":
    main()
