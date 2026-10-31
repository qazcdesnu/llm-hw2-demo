# 카테고리 정의 (Category Spec)

← [CLAUDE.md](../CLAUDE.md) · 관련: [architecture](architecture.md#4-데이터-파이프라인-오프라인) · [데이터 분석](data-analysis.md)

서비스가 다루는 상품 카테고리는 `backend/categories/<id>.yaml` 파일 하나로 정의한다.
속성 목록, 값, 질문 문구, 원본 데이터에서 값을 뽑는 규칙이 모두 이 파일에 있고,
추출·DB 저장·IG 엔진·LLM 파서는 카테고리별 코드 없이 이 정의만 읽어 동작한다.

```
categories/<id>.yaml ──▶ app/catalog/spec.py      (Pydantic으로 검증)
                    ──▶ app/catalog/extract.py   (규칙으로 원본 상품에서 속성 추출)
                    ──▶ app/catalog/repository.py (SQLite 저장, 정의 스냅샷 포함)
                    ──▶ IG 엔진 / LLM 파서        (속성·값·질문을 정의에서 읽음, 예정)
```

## 새 카테고리 추가 절차

1. `backend/categories/<id>.yaml` 작성 (아래 형식, [headphones.yaml](../backend/categories/headphones.yaml) 참고)
2. 원본 메타 파일을 `backend/data/raw/`에 받음 (HuggingFace `McAuley-Lab/Amazon-Reviews-2023`, `raw/meta_categories/`)
3. `uv run python scripts/filter_category.py <id>` → `data/interim/<id>.jsonl`
4. `uv run python scripts/build_catalog.py <id> --dry-run` → 속성별 커버리지 확인, 규칙 조정
5. `uv run python scripts/build_catalog.py <id>` → SQLite에 저장 + `data/catalog/<id>.json` 내보내기 (git에 포함). 원본 없이 이 JSON으로 DB를 만들 때는 `scripts/load_catalog.py <id>`
6. `uv run python scripts/build_embeddings.py <id>` → 상품 글 임베딩을 SQLite에 저장 (동률 정렬의 텍스트 관련도. 없으면 BM25만, [IG 엔진 4절](ig-engine.md#4-추천과-한계-표시))
7. `uv run python scripts/export_validation.py <id> --n 40` → `docs/validation/<id>_validation.csv` 수동 검증
8. `uv run pytest` (모든 yaml이 스키마 검증을 통과하는지 함께 확인됨)

## 파일 형식

```yaml
id: headphones                 # 파일명과 같아야 함, 소문자·숫자·_
name: 헤드폰·이어폰
name_en: headphones & earbuds     # 선택. 영어 사용자에게 보일 이름 (*_en이 없으면 한국어)
description: ...
source:
  raw_file: meta_Electronics.jsonl   # data/raw/ 아래 원본
  category: Headphones & Earbuds     # 상품 categories 배열에 이 항목이 있으면 포함
candidate_filter:
  require_price: true
  min_rating_number: 100
sampling:                      # 선택. 없으면 후보 전체를 카탈로그로 사용
  size: 150                    # null이면 require_known을 통과한 후보 전체
  seed: 42
  stratify_by: [connection, form_factor]   # 이 조합의 후보 비율대로 비례 층화 추출 (최대 잉여법)
  require_known: [connection, form_factor] # unknown인 상품은 제외
attributes:
  - key: connection            # DB·엔진에서 쓰는 이름
    label: 연결 방식            # 사용자에게 보이는 이름
    label_en: Connection       # 선택. 영어 사용자용
    question: 선 있는 것도 괜찮아, 아니면 무선이 좋아?   # askable이면 필수. 질문 작성기(LLM)가 없거나 실패할 때 쓰는 직접 질문
    question_en: Are you okay with a cable, or do you want wireless?   # 선택. 영어 사용자용
    askable: true              # false면 시스템이 먼저 묻지 않음 (사용자가 말하면 조건으로는 사용)
    ask_directly: false        # true면 질문 작성기가 돌려 묻지 않고 바로 물음 (예산처럼 생활 장면으로 갈리지 않는 속성)
    constraint: true           # false면 추천 조건으로 쓰지 않고 동률 정렬에만 씀 (값 하나로 정하기 어려운 속성, 예: 주 용도). askable과 함께 쓸 수 없음
    values:                    # 비우면 자유값 속성 (예: brand, 소문자로 저장)
      - {value: wireless, label: 무선, label_en: Wireless}
      - {value: wired, label: 유선, label_en: Wired}
    rules: [...]               # 위에서부터 적용, 처음 값을 낸 규칙 사용
    default: "no"              # 규칙이 모두 실패하고 features가 있을 때의 값 (없으면 unknown)
    unknown_if: noise[- ]?cancel   # 이 표현이 있으면 default로 단정하지 않고 unknown (애매한 언급 처리)
```

### 규칙 종류

| type | 대상 | 설명 |
|---|---|---|
| [`details`](../backend/app/catalog/spec.py "sym:py:app.catalog.spec#DetailsRule") | `details[keys]` | 처음 존재하는 키의 값에 `map` 정규식 적용. `map`이 없으면 값 자체를 사용 (자유값 속성만) |
| [`categories`](../backend/app/catalog/spec.py "sym:py:app.catalog.spec#CategoriesRule") | 카테고리 경로 | `" > "`로 이은 경로에 `map` 정규식 적용 |
| [`keywords`](../backend/app/catalog/spec.py "sym:py:app.catalog.spec#KeywordsRule") | `fields`의 텍스트 | 기본 `title, features, description`. `details`도 지정 가능 |
| [`band`](../backend/app/catalog/spec.py "sym:py:app.catalog.spec#BandRule") | 숫자 필드 | `bands`를 위에서부터 보고 `값 < max`인 첫 구간. 마지막은 `max` 생략 |

- 규칙 적용 순서·`default`·`unknown_if` 처리: [`extract_attribute`](../backend/app/catalog/extract.py "sym:py:app.catalog.extract#extract_attribute")
- `map`은 `값: 정규식`이고 위쪽 값이 우선한다. 정규식은 대소문자를 무시한다.
- YAML에서 `yes`/`no`는 불리언으로 읽히므로 반드시 따옴표로 감싼다 (`"yes"`).

### 검증 (로드 시 자동)
구현: [`AttributeSpec._check_consistency`](../backend/app/catalog/spec.py "sym:py:app.catalog.spec#AttributeSpec._check_consistency"), [`CategorySpec._check_keys`](../backend/app/catalog/spec.py "sym:py:app.catalog.spec#CategorySpec._check_keys")

- 규칙과 `default`가 내는 값은 모두 `values`에 선언되어 있어야 함
- 정규식 문법 오류 금지, 속성 key 중복 금지
- `askable` 속성은 `question` 필수, 자유값 속성은 `askable: false`
- `constraint: false` 속성은 `askable: false`여야 함 (엔진에 넣지 않으므로 물을 수 없음)
- `*_en`은 선택이다. 없으면 영어 사용자에게도 한국어 문구를 보여준다
- `question`은 대체용 직접 질문이다. 평소 질문은 질문 작성기가 대화에 맞춰 돌려 묻고, 돌려 묻는 질문은 정의에 적지 않는다([LLM 파서 5절](llm-parser.md#5-질문-작성기-친구-페르소나))
- 서버는 DB에 저장된 정의 스냅샷을 읽는다. 질문 문구 등 정의를 고치면 `uv run python scripts/load_catalog.py <id>`(또는 `build_catalog.py`)로 다시 넣는다

샘플링 구현: [`stratified_sample`](../backend/app/catalog/sampling.py "sym:py:app.catalog.sampling#stratified_sample"), [`allocate`](../backend/app/catalog/sampling.py "sym:py:app.catalog.sampling#allocate")

## DB 구조
전체 모델: [architecture 5절](architecture.md#5-데이터-모델-sqlite)

- `categories`: 카테고리 정의 JSON 스냅샷. 런타임은 이 스냅샷을 읽어 DB의 상품 속성과 정의 버전이 항상 일치
- `products`: 카테고리와 무관한 공통 필드 (제목, 가격, 평점, 이미지, 원문 features/description/details)
- `product_attributes`: `(product_id, attribute) → value, source, evidence`. 행이 없으면 unknown. 추천 근거로 `evidence`(원문 일부)를 보여줄 수 있음

## 현재 제약
- 속성은 상품당 값 하나 (단일값). `use_case`처럼 여러 값이 가능한 속성은 우선순위가 높은 값 하나만 저장한다.
