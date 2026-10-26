"""카테고리 정의의 규칙으로 원본 상품(Amazon 메타 JSON)에서 속성 값을 뽑는다.

@see docs/category-spec.md#규칙-종류
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from app.catalog.spec import (
    TEXT_FIELDS, AttributeSpec, BandRule, CategoriesRule, CategorySpec, DetailsRule, KeywordsRule,
    Rule,
)

EVIDENCE_CONTEXT = 40


@dataclass(frozen=True)
class Extracted:
    value: str
    source: str  # 값을 낸 규칙 종류 (details, categories, keywords, band, default)
    evidence: str | None = None  # 근거가 된 원문 일부


@lru_cache(maxsize=None)
def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def _snippet(text: str, match: re.Match) -> str:
    start = max(match.start() - EVIDENCE_CONTEXT, 0)
    end = min(match.end() + EVIDENCE_CONTEXT, len(text))
    return " ".join(text[start:end].split())


def _first_match(patterns: dict[str, str], text: str) -> tuple[str, str] | None:
    for value, pattern in patterns.items():
        m = _compile(pattern).search(text)
        if m:
            return value, _snippet(text, m)
    return None


def _field_text(item: dict, field: str) -> str:
    raw = item.get(field)
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return " ".join(f"{k}: {v}" for k, v in raw.items())
    if isinstance(raw, list):
        return " ".join(str(x) for x in raw)
    return str(raw)


def _apply(rule: Rule, item: dict) -> Extracted | None:
    if isinstance(rule, DetailsRule):
        details = item.get("details") or {}
        for key in rule.keys:
            raw = details.get(key)
            if raw is None or not str(raw).strip():
                continue
            text = str(raw)
            if rule.map is None:
                return Extracted(" ".join(text.split()).casefold(), "details", f"{key}: {text}")
            hit = _first_match(rule.map, text)
            if hit:
                return Extracted(hit[0], "details", f"{key}: {text}")
        return None

    if isinstance(rule, CategoriesRule):
        hit = _first_match(rule.map, " > ".join(item.get("categories") or []))
        return Extracted(hit[0], "categories", hit[1]) if hit else None

    if isinstance(rule, KeywordsRule):
        text = " ".join(_field_text(item, f) for f in rule.fields)
        hit = _first_match(rule.map, text)
        return Extracted(hit[0], "keywords", hit[1]) if hit else None

    if isinstance(rule, BandRule):
        raw = item.get(rule.field)
        if not isinstance(raw, (int, float)):
            return None
        for band in rule.bands:
            if band.max is None or raw < band.max:
                return Extracted(band.value, "band", f"{rule.field}: {raw}")
        return None

    raise TypeError(f"unknown rule: {rule!r}")


def extract_attribute(attr: AttributeSpec, item: dict) -> Extracted | None:
    """속성 값. 근거가 없으면 None(unknown)."""
    for rule in attr.rules:
        result = _apply(rule, item)
        if result is not None:
            return result
    if attr.default is None or not item.get("features"):
        return None
    if attr.unknown_if is not None:
        text = " ".join(_field_text(item, f) for f in TEXT_FIELDS)
        if _compile(attr.unknown_if).search(text):
            return None
    return Extracted(attr.default, "default")


def extract_attributes(spec: CategorySpec, item: dict) -> dict[str, Extracted]:
    results = {}
    for attr in spec.attributes:
        extracted = extract_attribute(attr, item)
        if extracted is not None:
            results[attr.key] = extracted
    return results
