"""카테고리 정의(categories/*.yaml)의 스키마.

카테고리가 다루는 속성, 값, 질문 문구, 원본 데이터에서 값을 뽑는 규칙을 한 파일에 담는다.
추출(extract.py), DB 저장(repository.py), IG 엔진, LLM 파서는 모두 이 정의만 보고 동작한다.
"""

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

CATEGORIES_DIR = Path(__file__).resolve().parents[2] / "categories"

TEXT_FIELDS = ["title", "features", "description"]


def _check_patterns(patterns: dict[str, str]) -> dict[str, str]:
    for value, pattern in patterns.items():
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"invalid regex for {value!r}: {e}") from e
    return patterns


class DetailsRule(BaseModel):
    """details의 keys 중 처음 있는 키 값을 본다. map이 없으면 값을 그대로(자유값) 쓴다.

    @see docs/category-spec.md#규칙-종류
    """

    type: Literal["details"]
    keys: list[str]
    map: dict[str, str] | None = None

    _validate_map = field_validator("map")(lambda cls, v: v and _check_patterns(v))


class CategoriesRule(BaseModel):
    """categories 경로(" > "로 이은 문자열)에 정규식을 적용한다.

    @see docs/category-spec.md#규칙-종류
    """

    type: Literal["categories"]
    map: dict[str, str]

    _validate_map = field_validator("map")(lambda cls, v: _check_patterns(v))


class KeywordsRule(BaseModel):
    """fields의 텍스트에 정규식을 적용한다. map의 위쪽 값이 우선한다.

    @see docs/category-spec.md#규칙-종류
    """

    type: Literal["keywords"]
    fields: list[str] = Field(default_factory=lambda: list(TEXT_FIELDS))
    map: dict[str, str]

    _validate_map = field_validator("map")(lambda cls, v: _check_patterns(v))


class Band(BaseModel):
    value: str
    max: float | None = None  # 이 값 미만. None이면 상한 없음


class BandRule(BaseModel):
    """숫자 필드를 구간으로 나눈다. bands는 max 오름차순.

    @see docs/category-spec.md#규칙-종류
    """

    type: Literal["band"]
    field: str
    bands: list[Band]


Rule = Annotated[
    DetailsRule | CategoriesRule | KeywordsRule | BandRule, Field(discriminator="type")
]


class ValueSpec(BaseModel):
    value: str
    label: str


class AttributeSpec(BaseModel):
    key: str
    label: str
    question: str | None = None
    # False면 시스템이 먼저 묻지 않는다. 사용자가 말한 조건으로는 쓴다.
    askable: bool = True
    # 비어 있으면 자유값 속성(예: brand). 값은 소문자로 정규화해 저장한다.
    values: list[ValueSpec] = []
    # 위에서부터 적용해 처음 값을 내는 규칙을 쓴다.
    rules: list[Rule]
    # 어떤 규칙도 맞지 않을 때의 값. 상품 설명(features)이 있을 때만 적용하고, 없으면 unknown.
    default: str | None = None
    # 규칙이 모두 실패했을 때 TEXT_FIELDS에 이 정규식이 있으면 default를 쓰지 않고 unknown으로 둔다.
    # 근거가 애매한 언급이 있는 상품을 default 값으로 단정하지 않기 위함.
    unknown_if: str | None = None

    @field_validator("unknown_if")
    @classmethod
    def _check_unknown_if(cls, v: str | None) -> str | None:
        if v is not None:
            _check_patterns({"unknown_if": v})
        return v

    @property
    def is_open(self) -> bool:
        return not self.values

    @model_validator(mode="after")
    def _check_consistency(self) -> "AttributeSpec":
        """@see docs/category-spec.md#검증-로드-시-자동"""
        if self.askable and not self.question:
            raise ValueError(f"{self.key}: askable 속성에는 question이 필요하다")
        if self.is_open:
            if self.askable:
                raise ValueError(f"{self.key}: 자유값 속성은 askable일 수 없다")
            return self

        produced: set[str] = set()
        for rule in self.rules:
            if isinstance(rule, BandRule):
                produced |= {b.value for b in rule.bands}
            elif rule.map is None:
                raise ValueError(f"{self.key}: 값 목록이 있는 속성의 details 규칙에는 map이 필요하다")
            else:
                produced |= set(rule.map)
        if self.default is not None:
            produced.add(self.default)
        undeclared = produced - {v.value for v in self.values}
        if undeclared:
            raise ValueError(f"{self.key}: values에 없는 값 {sorted(undeclared)}")
        return self


class SourceSpec(BaseModel):
    raw_file: str  # data/raw/ 아래 Amazon Reviews 2023 메타 파일
    category: str  # 상품의 categories 배열에 들어 있어야 하는 항목


class CandidateFilter(BaseModel):
    require_price: bool = True
    min_rating_number: int = 0


class SamplingSpec(BaseModel):
    """후보 중 카탈로그에 넣을 상품을 뽑는 방법. stratify_by 조합의 실제 비율을 유지한다."""

    # None이면 조건(require_known)을 통과한 후보를 모두 쓴다.
    size: int | None = Field(default=None, gt=0)
    seed: int = 42
    stratify_by: list[str] = []
    # 이 속성들이 unknown인 상품은 뽑지 않는다.
    require_known: list[str] = []


class CategorySpec(BaseModel):
    """@see docs/architecture.md#1-한눈에-보기"""
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str = ""
    source: SourceSpec
    candidate_filter: CandidateFilter = CandidateFilter()
    sampling: SamplingSpec | None = None
    attributes: list[AttributeSpec]

    @model_validator(mode="after")
    def _check_keys(self) -> "CategorySpec":
        """@see docs/category-spec.md#검증-로드-시-자동"""
        keys = [a.key for a in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{self.id}: 속성 key 중복")
        if self.sampling:
            missing = set(self.sampling.stratify_by + self.sampling.require_known) - set(keys)
            if missing:
                raise ValueError(f"{self.id}: sampling에 없는 속성 {sorted(missing)}")
        return self

    def attribute(self, key: str) -> AttributeSpec:
        for a in self.attributes:
            if a.key == key:
                return a
        raise KeyError(key)


def load_category_spec(category_id: str, directory: Path = CATEGORIES_DIR) -> CategorySpec:
    path = directory / f"{category_id}.yaml"
    spec = CategorySpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if spec.id != category_id:
        raise ValueError(f"{path.name}: 파일명과 id({spec.id})가 다르다")
    return spec


def load_all_category_specs(directory: Path = CATEGORIES_DIR) -> dict[str, CategorySpec]:
    return {p.stem: load_category_spec(p.stem, directory) for p in sorted(directory.glob("*.yaml"))}
