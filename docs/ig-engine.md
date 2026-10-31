# IG 엔진

← [CLAUDE.md](../CLAUDE.md) · 관련: [architecture](architecture.md) · [roadmap Phase 2](roadmap.md#phase-2-information-gain-엔진) · [roadmap Phase 3+](roadmap.md#phase-3-기능-고도화-암묵적-선호-추론과-추천-보정)

목표 상품에 대한 확률분포(belief)를 유지하면서 질문을 고르는 엔진.
웹·DB·LLM과 무관한 순수 Python이고([`app.core`](../backend/app/core/__init__.py "sym:py:app.core")), API와 평가 시뮬레이터가 같은 코드를 쓴다.

| 모듈 | 역할 |
|---|---|
| [`model`](../backend/app/core/model.py "sym:py:app.core.model") | 속성·상품·관측·대화 상태 |
| [`belief_tracker`](../backend/app/core/belief_tracker.py "sym:py:app.core.belief_tracker") | 가능도 모델, belief 계산 |
| [`question_selector`](../backend/app/core/question_selector.py "sym:py:app.core.question_selector") | 기대 정보량, 질문 정책(IG / Random / Fixed) |
| [`recommender`](../backend/app/core/recommender.py "sym:py:app.core.recommender") | Top-k, 조건 충족 여부 |
| [`engine`](../backend/app/core/engine.py "sym:py:app.core.engine") | 한 턴의 결정(질문 / 추천), 종료 조건, 한계 표시 |
| [`adapters`](../backend/app/core/adapters.py "sym:py:app.core.adapters") | 카탈로그 → 엔진 입력 변환 |

## 1. 답변 가능도 모델
([I-3](issue.md#i-3)의 결정)

가설 공간은 "사용자가 원하는 상품 T"이다. 사전분포는 균등이다.
속성 a에 대한 증거 "a = v"(확신도 c)가 주어졌을 때 상품 p의 가능도([`likelihood`](../backend/app/core/belief_tracker.py "docid:core.likelihood"))는 아래와 같다. k는 속성 값의 개수다.

| 상품 p의 속성 값 | P(증거 \| T = p) |
|---|---|
| v와 같음 | c |
| v와 다름 | (1 − c) / (k − 1) |
| unknown | 1 / k |

- **직접 답한 조건:** c = 1 − ε이다. ε는 응답 노이즈로 기본 0.05다. 사용자가 실수하거나 상품 데이터가 틀릴 가능성을 반영하며, 0이면 한 번의 오답으로 목표 상품이 영원히 탈락한다.
- **c < 1/k:** 1/k로 올린다. 그보다 낮으면 일치하는 상품이 오히려 불리해지기 때문이다.
- **LLM 확신도는 쓰지 않는다 (2026-10-31):** 대화에서는 속성 추출기가 뽑은 값도 직접 답과 같은 c = 1 − ε로 넣는다. 처음에는 LLM이 매긴 확신도를 c로 쓰는 추정 선호가 있었으나 없앴다([roadmap Phase 3+](roadmap.md#phase-3-기능-고도화-암묵적-선호-추론과-추천-보정)).
- **unknown 상품:** 어떤 답에도 똑같이 반응한다. 그래서 그 속성의 답으로는 확률이 오르지도 내리지도 않는다. 속성 정보가 없는 상품을 함부로 탈락시키지 않는다.
- **"상관없음":** 상품과 무관한 답이라 belief를 바꾸지 않는다. 그 속성은 다시 묻지 않는다.
- **belief 계산:** belief = 사전분포 × Π 가능도를 정규화한 값이다([`compute_belief`](../backend/app/core/belief_tracker.py "docid:core.compute-belief")). 관측 순서와 무관하다.

## 2. 대화 상태

[`PreferenceState`](../backend/app/core/model.py "docid:core.preference-state")가 엔진이 쓰는 "지금까지 알게 된 것"이다.
대화에서는 저장하지 않고 대화 메모리에서 매 턴 만든다([architecture 세션 상태](architecture.md#세션-상태)). 평가 시뮬레이터는 직접 만든다.

```json
{
  "explicit": {"connection": "wireless", "anc": "yes"},
  "dont_care": ["brand"],
  "asked": ["connection", "use_case"],
  "excluded": []
}
```

- **belief는 저장하지 않는다.** 매 턴 이 상태에서 다시 계산한다. 상품 4,628개 기준으로 DB 읽기, 엔진 생성, 한 턴 결정을 합쳐 약 70ms다.
- **정정:** 같은 속성에 다시 답하면 이전 값을 바꾸고, 그 상태로 belief를 다시 계산한다. 이전 조건이 누적되지 않는다.
- **질문 예산:** `asked`(엔진이 물은 속성)로 센다. 사용자가 먼저 말한 조건은 예산을 쓰지 않는다. "상관없음"이라는 답도 질문 하나를 쓴 것으로 센다.
  - 대화에서는 답이 안 나와 다시 물을 수 있는 질문은 `asked`에 넣지 않고, 두 번 물어 넘어간 질문은 넣는다([LLM 파서 6절](llm-parser.md#6-질문에-답하지-않은-말)).

## 3. 질문 선택

**기대 정보량** ([`expected_information_gain`](../backend/app/core/question_selector.py "docid:core.expected-ig"))
- 속성 a를 물었을 때 목표 상품에 대해 얻을 상호정보량이다.
  - I(T; A) = Σ_p Σ_v P(p)·L(v|p)·log₂(L(v|p) / P(v)),  P(v) = Σ_p P(p)·L(v|p)
- 답변은 1절과 같은 모델(확신도 1 − ε)로 생성된다고 본다.
- 노이즈가 없고 값이 고르게 나뉘면 log₂k bits다. 속성 값이 같거나 unknown뿐인 후보들 사이에서는 0이다.

**질문 후보** ([`candidate_questions`](../backend/app/core/question_selector.py "sym:py:app.core.question_selector#candidate_questions"))
- `askable`이고, 아직 답하지 않았고, 아직 묻지 않은 속성이다.

**정책** (평가에서 이것만 바꿔 비교한다)

| 정책 | 선택 |
|---|---|
| [`InformationGainPolicy`](../backend/app/core/question_selector.py "sym:py:app.core.question_selector#InformationGainPolicy") | 기대 정보량 최대 |
| [`RandomPolicy`](../backend/app/core/question_selector.py "sym:py:app.core.question_selector#RandomPolicy") | 무작위 (seed 고정 가능) |
| [`FixedOrderPolicy`](../backend/app/core/question_selector.py "sym:py:app.core.question_selector#FixedOrderPolicy") | 정해진 순서. 기본은 YAML의 속성 순서 |

**조건이 질문 순서에 주는 영향**

조건(답)은 belief만 바꾸고, IG는 바뀐 belief로 다시 계산된다. 이미 답한 속성은 후보에서 빠지고, 남은 속성의 IG는 좁혀진 후보 위에서 다시 계산된다.
(LLM 확신도로 약한 증거를 넣던 시절에는 "드문 값을 추정하면 그 속성의 IG가 올라 확인 질문이 앞선다"는 효과가 있었다. 확신도를 없애면서 이 효과도 없어졌다.)

## 4. 추천과 한계 표시

[`IGEngine.step`](../backend/app/core/engine.py "docid:core.engine-step")은 매 턴 질문(`Ask`)과 추천(`Recommend`) 중 하나를 돌려준다. 상태는 바꾸지 않는다.

**종료 조건** ([`EngineConfig`](../backend/app/core/engine.py "sym:py:app.core.engine#EngineConfig"))

| reason | 조건 | 기본값 |
|---|---|---|
| `budget` | 엔진이 물은 질문 수 ≥ 예산 | 4 |
| `confident` | Top-k 확률 합 ≥ 임계값 | k = 3, 0.9 |
| `no_informative_question` | 남은 질문의 기대 정보량이 모두 임계값 미만. 수집 가능한 속성으로는 후보를 더 구별할 수 없음 | 0.05 bits |

**추천** ([`recommend`](../backend/app/core/recommender.py "docid:core.recommend"))
- **정렬:** 확률 내림차순이다. 확률이 같은(조건이 똑같이 맞는) 상품 사이의 순서는 대화 관리자가 `tie_key`로 준다. 주지 않으면 인기도 점수 순이다.
  - 인기도 점수는 평점의 베이지안 평균이다([`popularity_scores`](../backend/app/core/adapters.py "sym:py:app.core.adapters#popularity_scores")). 확률 계산에는 쓰지 않는다.
- **조건 위반 처리:** 사용자가 말한 조건(속성 추출기가 뽑은 값 포함)을 어기는 상품은 뺀다(Safety: 요구와 무관한 추천 방지). 그런 상품을 빼면 k개가 안 될 때만 뒤에 채우고 `violated`로 표시한다.
- **추천마다 붙는 정보:** `matched`(맞음), `unknown`(데이터에 없어 확인 못 함), `violated`(어긋남)를 붙인다. 대화 관리자가 이를 추천 근거 문장으로 바꾼다.

**추천 후 조정** ([`IGEngine.recommend_now`](../backend/app/core/engine.py "docid:core.engine-recommend-now"))
- 질문 없이 지금 상태로 바로 추천한다(reason `refined`).
- `PreferenceState.excluded`의 상품은 사전확률을 0으로 둔다. 모두 제외되면 제외를 무시한다.
- `tie_key`로 확률이 같은 상품 사이의 순서를 바꾼다(예: 가격 오름차순). 확률 계산 자체는 바꾸지 않는다.
- 사용 방법: [LLM 파서 9절](llm-parser.md#9-멀티턴-추천-조정)

**동률 정렬 (2026-10-31)** ([`tie_order`](../backend/app/services/chat.py "docid:chat.tie-order"))

속성 8개로는 같은 조건의 상품이 수십~수백 개씩 남는다. 질문은 속성으로만 고르되, 그 안의 순서는 상품 정보 전체로 정한다. 추천할 때만 계산한다(질의 임베딩 호출 1회, [`decide`](../backend/app/services/chat.py "sym:py:app.services.chat#decide")).

| 순서 | 기준 | 비고 |
|---|---|---|
| 1 | 사용자가 요청한 가격순 | "더 싼 걸로" ([LLM 파서 9절](llm-parser.md#9-멀티턴-추천-조정)) |
| 2 | 조건이 아닌 속성(`constraint: false`, 헤드폰은 주 용도)이 말한 값과 맞는가 | 맞지 않는 개수가 적은 순 |
| 3 | 텍스트 관련도: BM25 + 임베딩 순위를 RRF(k = 60)로 합침 | [`relevance_scores`](../backend/app/services/relevance.py "docid:chat.text-relevance") |
| 4 | 인기도 점수 | |

- **상품 글:** 상품명 + 특징(features) + 세부 정보(details, 판매 순위·출시일 등 제외), 최대 6,000자([`product_text`](../backend/app/services/relevance.py "sym:py:app.services.relevance#product_text")).
- **BM25 질의:** 속성 추출기가 매 턴 뽑는 영어 키워드(메모리 `keywords`). 상품 글이 영어라, 한국어 대화도 키워드는 영어로 뽑는다.
- **임베딩:** 상품 임베딩은 미리 만들어 SQLite `product_embeddings`에 저장한다([build_embeddings.py](../backend/scripts/build_embeddings.py), `text-embedding-3-small` 512차원, 4,628개). 실행 중에는 질의 글(근거 발화 + 맥락 + 키워드) 하나만 임베딩한다. 색인은 카탈로그·임베딩이 바뀔 때까지 캐시한다([`load_text_index`](../backend/app/services/relevance.py "sym:py:app.services.relevance#load_text_index")). 임베딩이 없거나 호출이 실패하면 BM25만 쓴다.
- **주 용도를 조건에서 뺀 이유:** 상품도 사용자도 용도 하나로 정하기 어렵다(업무용으로 산 게이밍 헤드셋, 운동에도 쓰는 일상용 헤드폰). 평가에서 정답이 1위 동률 묶음 밖으로 밀린 7건 중 5건이 주 용도 불일치였다([evaluation.md 5절](evaluation.md#5-결과)). 그래서 묻지 않고(askable: false) 거르지도 않으며, 사용자가 말하면 정렬에만 쓴다.
- 사용자에게는 "N개가 똑같이 잘 맞아서, 얘기해 준 내용에 가까운 순으로 골랐어"로 알린다. Developer Panel의 `Tie order`에 기준이 보인다.

**한계** ([`Limitation`](../backend/app/core/engine.py "sym:py:app.core.engine#Limitation")): 대화 관리자가 문장으로 바꿔 사용자에게 알린다.

| code | 의미 |
|---|---|
| `tied_candidates` | 1위와 같은 확률의 상품이 추천 개수보다 많다. 수집한 속성으로는 구별할 수 없다 |
| `unknown_attributes` | 추천 상품 중 사용자 조건을 데이터로 확인할 수 없는 것이 있다 |
| `constraint_relaxed` | 조건을 모두 만족하는 상품이 모자라 일부 조건을 어기는 상품을 포함했다 |

## 5. 실행해 보기

[ig_demo.py](../backend/scripts/ig_demo.py): 실제 카탈로그(DB)로 질문과 추천 과정을 출력한다.

```bash
cd backend
uv run python scripts/ig_demo.py headphones --target B0BHR5VQC9          # 이 상품의 속성대로 답하는 사용자
uv run python scripts/ig_demo.py headphones --answers connection=wireless,anc=yes
uv run python scripts/ig_demo.py headphones --interactive
uv run python scripts/ig_demo.py headphones --target B0BHR5VQC9 --policy random --seed 1
```

## 6. 예비 확인 결과 (2026-10-26)

> **150개 카탈로그 기준 결과다.** 카탈로그를 4,628개로 넓힌 뒤에는 처음 엔트로피가 12.18 bits이고, 첫 질문의 IG 순위는 같다(use_case 1.32 > price_band 1.22 > form_factor 1.16 > …). 정식 측정은 Phase 4에서 4,628개로 다시 한다.

헤드폰 카탈로그 150개의 각 상품을 목표로, 목표의 속성대로 답하는 사용자와 대화했다. 목표 속성이 unknown이면 "상관없음"으로 답했다.
엔진 동작 확인용이다. 정식 평가(지표 정의, 노이즈, 반복)는 [roadmap Phase 4](roadmap.md#phase-4-정량-평가)에서 한다.

| 질문 수 | IG Hit@3 | Fixed Hit@3 | Random Hit@3 | IG Hit@1 | IG 남은 엔트로피 |
|---|---|---|---|---|---|
| 1 | 0.080 | 0.040 | 0.040 | 0.027 | 6.02 bits |
| 2 | 0.233 | 0.133 | 0.087 | 0.080 | 4.82 bits |
| 3 | 0.453 | 0.193 | 0.173 | 0.193 | 3.80 bits |
| 4 | **0.627** | 0.460 | 0.320 | 0.300 | 3.26 bits |

- 질문 수마다 IG가 두 baseline보다 높다. 처음 엔트로피는 7.23 bits(상품 150개)다.
- 첫 질문의 IG 순위: use_case 1.29 > price_band 1.21 > form_factor 1.17 > connection 0.67 > anc 0.35 > water_resistant 0.18 > mic 0.15.
  - 값이 여러 개이고 고르게 나뉜 속성이 앞선다.
  - mic·방수가 낮은 이유는 "no" 값이 없어서다([I-7](issue.md#i-7)).
- 4번 물어도 같은 확률의 후보가 여러 개 남는 경우가 많다(예: 9개 동률). 속성이 거칠어 상품 단위로는 구별되지 않기 때문이다.
  - 계획서의 지적대로 Hit@k 외에 조건 충족률도 함께 봐야 한다.
- "추천 3개 중 조건 위반이 없는 비율"은 IG가 0.92로 baseline(0.98~0.99)보다 낮다.
  - IG가 조건을 더 많이 좁혀서, 조건을 모두 만족하는 상품이 3개 미만인 경우가 생기기 때문이다.
  - 지표를 어떻게 정의할지는 [I-4](issue.md#i-4)에서 정한다.
