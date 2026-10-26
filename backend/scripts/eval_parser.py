"""LLM 파서를 평가 세트로 채점하고 모델·추론 강도별로 비교한다 (실제 API 호출).

    uv run python scripts/eval_parser.py                                   # .env 설정 하나
    uv run python scripts/eval_parser.py --configs gpt-5-nano:minimal gpt-4.1-nano: gpt-5-mini:minimal
    uv run python scripts/eval_parser.py --verbose                         # 틀린 케이스 출력

설정 형식: <모델>:<reasoning effort> (비추론 모델은 effort를 비움)
채점: request_type 일치 + 기대 explicit 조건 모두 포함 + 기대 밖 explicit 없음 + forbid 없음

@see docs/llm-parser.md#3-평가
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.spec import load_category_spec  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import LLMError, OpenAIClient  # noqa: E402
from app.services.parser import parse_message  # noqa: E402

CASES = Path(__file__).resolve().parent.parent / "evaluation" / "parser_cases.yaml"

# 1M 토큰당 (입력, 출력) 달러. https://developers.openai.com/api/docs/pricing (2026-10-26 확인)
PRICES = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4o-mini": (0.15, 0.60),
}


class MeteredClient(OpenAIClient):
    """토큰 사용량을 모으는 클라이언트."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.input_tokens = self.output_tokens = 0

    def _log_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens


@dataclass
class CaseResult:
    case: dict
    passed: bool
    errors: list[str] = field(default_factory=list)
    inferred_hits: int = 0
    seconds: float = 0.0


def grade(case: dict, result) -> CaseResult:
    errors = []
    if result.request_type != case["request"]:
        errors.append(f"request {result.request_type} != {case['request']}")
    explicit = {c.attribute: c.value for c in result.constraints if c.kind == "explicit"}
    produced = {c.attribute: c.value for c in result.constraints}
    want = {k: str(v) for k, v in (case.get("explicit") or {}).items()}
    allow = {k: [str(x) for x in v] for k, v in (case.get("allow") or {}).items()}
    for k, v in want.items():
        if explicit.get(k) != v:
            errors.append(f"missing {k}={v} (got {produced.get(k)})")
    for k, v in explicit.items():
        if k not in want and v not in allow.get(k, []):
            errors.append(f"extra {k}={v}")
    for k in case.get("forbid") or []:
        if k in produced:
            errors.append(f"forbidden {k}={produced[k]}")
    if "price_order" in case and result.price_order != case["price_order"]:
        errors.append(f"price_order {result.price_order} != {case['price_order']}")
    if "exclude" in case and result.exclude_ranks != case["exclude"]:
        errors.append(f"exclude {result.exclude_ranks} != {case['exclude']}")
    inferred = {k: str(v) for k, v in (case.get("inferred") or {}).items()}
    hits = sum(1 for k, v in inferred.items() if produced.get(k) == v)
    return CaseResult(case, not errors, errors, hits)


def run_config(model: str, effort: str | None, spec, cases: list[dict], workers: int):
    client = MeteredClient(settings.openai_api_key.get_secret_value(), model,
                           reasoning_effort=effort, timeout=60)

    def one(case: dict) -> CaseResult:
        start = time.perf_counter()
        try:
            known = {k: str(v) for k, v in (case.get("known") or {}).items()}
            result = parse_message(client, spec, case["message"], case.get("pending"), known,
                                   case.get("current"))
            graded = grade(case, result)
        except LLMError as e:
            graded = CaseResult(case, False, ["request: LLM error", f"{e}"[:200]])
        graded.seconds = time.perf_counter() - start
        return graded

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, cases))
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    cost = (client.input_tokens * price_in + client.output_tokens * price_out) / 1e6
    return results, client, cost


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", nargs="*",
                        default=[f"{settings.openai_model}:{settings.openai_reasoning_effort or ''}"])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    data = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    spec = load_category_spec(data["category"])
    cases = data["cases"]
    n_inferred = sum(len(c.get("inferred") or {}) for c in cases)

    rows = []
    for config in args.configs:
        model, _, effort = config.partition(":")
        results, client, cost = run_config(model, effort or None, spec, cases, args.workers)
        passed = sum(r.passed for r in results)
        request_ok = sum(not any(e.startswith("request") for e in r.errors) for r in results)
        unsafe = [r for r in results if r.case["request"] == "unsafe"]
        unsafe_ok = sum(not any(e.startswith("request") for e in r.errors) for r in unsafe)
        inferred = sum(r.inferred_hits for r in results)
        latency = sorted(r.seconds for r in results)[len(results) // 2]
        rows.append((config, passed, request_ok, unsafe_ok, len(unsafe), inferred, latency, cost))
        if args.verbose:
            print(f"\n== {config}")
            for r in results:
                if not r.passed:
                    print(f"  ✗ [{r.case.get('pending') or '-'}] {r.case['message']}: {'; '.join(r.errors)}")

    n = len(cases)
    print(f"\n{'config':24} {'pass':>9} {'request':>9} {'unsafe':>7} {'inferred':>9} {'p50 s':>6} {'$/1k turns':>11}")
    for config, passed, request_ok, unsafe_ok, n_unsafe, inferred, latency, cost in rows:
        print(f"{config:24} {passed:>4}/{n:<4} {request_ok:>4}/{n:<4} {unsafe_ok:>3}/{n_unsafe:<3} "
              f"{inferred:>4}/{n_inferred:<4} {latency:>6.1f} {cost / n * 1000:>11.3f}")


if __name__ == "__main__":
    main()
