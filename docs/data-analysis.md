# 헤드폰 데이터 분석 (2026-10-26)

← [CLAUDE.md](../CLAUDE.md) · 관련: [카테고리 정의](category-spec.md) · [architecture](architecture.md#4-데이터-파이프라인-오프라인)

원본: Amazon Reviews 2023 `meta_Electronics.jsonl` (1,610,012개 상품, 5.25GB, 전 줄 JSON 파싱 확인)
스크립트: [filter_category.py](../backend/scripts/filter_category.py), [analyze_headphones.py](../backend/scripts/analyze_headphones.py)
산출물: `backend/data/interim/headphones.jsonl`, `analysis.json`, `analysis.txt`

## 1. 규모
카테고리 경로 `Headphones, Earbuds & Accessories > Headphones & Earbuds` 기준으로 걸렀다.

| 집합 | 상품 수 |
|---|---|
| 헤드폰·이어폰 전체 | 56,048 |
| 후보 집합 (가격 있음 + 리뷰 100개 이상) | 5,099 |

- 세부 분류(후보 집합): Earbud 3,225 / Over-Ear 1,296 / On-Ear 436 / Open-Ear 40 / 없음 102
- 150개를 고르기에는 충분하다. 후보 집합에서 고르면 가격 결측 문제도 없다.

## 2. 필드 품질

| 항목 | 전체 | 후보 집합 |
|---|---|---|
| 가격 있음 | 22.1% | 100% |
| features 있음 | 75.3% | 97.0% |
| description 있음 | 56.8% | 45.9% |
| 중복 제목 | 3,524 | 190 |

- 가격은 전체의 78%가 없어서, 후보 집합으로 한정하는 것이 사실상 필수다.
- 후보 집합의 가격 분위: p25 $18, 중앙값 $30, p75 $61, p90 $140 → 3구간(~$20 / $20~60 / $60~)으로 나누기 좋다.
- 중복 제목(색상 변형 등)은 제거해야 한다.

## 3. 구조화 속성 (`details`, 후보 집합 커버리지)

| details 키 | 커버리지 | 값 |
|---|---|---|
| Brand | 93.7% | Sony, JBL, Samsung, Skullcandy, JVC ... (표기 정규화 필요: SAMSUNG/Samsung) |
| Form Factor | 89.0% | In Ear 2,573 / Over Ear 1,025 / On Ear 683 / Open Ear 30 (Closed-back 등 잡음 소수) |
| Connectivity Technology | 87.9% | Wired 1,741 / Wireless 1,607 / Bluetooth 466 / 복합값 소수 |

→ 착용 방식, 연결 방식, 브랜드는 `details`에서 바로 얻을 수 있다. 값 정규화만 필요하다.
나머지 키(Special Feature, Charging Time, Ear Placement 등)는 커버리지가 15% 미만이라 단독으로는 쓰기 어렵다.

## 4. 텍스트 키워드 증거 (title + features + description + details, 후보 집합)

| 속성 후보 | 비율 | 비고 |
|---|---|---|
| 마이크 | 64.1% | |
| 배터리 시간 언급 | 50.7% | 사실상 무선 여부와 겹침 |
| 운동용 | 36.2% | |
| 방수·방진 (IPX 등) | 29.7% | |
| 노이즈 캔슬링 (넓게) | 29.8% | "noise cancelling mic"까지 걸림 |
| ANC (active noise, anc, hybrid) | 9.6% | |
| 어린이용 | 12.8% | |
| 접이식 | 11.8% | |
| 게이밍 | 8.3% | |

- ANC는 후보 집합에서도 약 10%로 드물다. 150개를 뽑을 때 ANC 상품 비율을 의도적으로 높여(층화 샘플링) 질문할 가치가 있게 만들어야 한다.
- 텍스트의 "wired" 키워드는 무선 헤드폰의 3.5mm 보조 단자 설명에도 걸려서(무선과 동시 매칭이 많음), 연결 방식은 `details`를 우선 쓰고 텍스트는 보조로만 써야 한다.

## 5. 속성 후보 (제안)

| 속성 | 값 | 출처 |
|---|---|---|
| connection | wired / wireless | details 우선, 텍스트 보조 |
| form_factor | in_ear / on_ear / over_ear / open_ear | details 우선, 카테고리 경로 보조 |
| anc | yes / no | 텍스트 (active noise, anc) |
| price_band | low / mid / high | 가격 분위 |
| mic | yes / no | 텍스트 |
| water_resistant | yes / no | 텍스트 (IPX, waterproof, sweatproof) |
| use_case | sports / gaming / kids / general | 텍스트 |
| brand | 상위 브랜드 + other | details (선택 질문) |

6~8개를 확보하면 질문 예산(4회)보다 속성이 많아져 IG와 Random·Fixed의 차이가 드러난다 ([I-1](issue.md#i-1)).
brand는 값이 많아 IG가 과대평가할 수 있으므로 질문 후보에 넣을지는 엔진 단계에서 결정한다.

이 제안은 [headphones.yaml](../backend/categories/headphones.yaml)로 정의되었다. 형식은 [category-spec.md](category-spec.md) 참고.
