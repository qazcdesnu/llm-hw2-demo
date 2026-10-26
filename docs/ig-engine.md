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
속성 a에 대한 증거 "a = v"(확신도 c)가 주어졌을 때 상품 p의 가능도([`likelihood`](../backend/app/core/belief_tracker.py#L12 "docid:core.likelihood"))는 아래와 같다. k는 속성 값의 개수다.

| 상품 p의 속성 값 | P(증거 \| T = p) |
|---|---|
| v와 같음 | c |
| v와 다름 | (1 − c) / (k − 1) |
| unknown | 1 / k |

- **직접 답한 조건:** c = 1 − ε이다. ε는 응답 노이즈로 기본 0.05다. 사용자가 실수하거나 상품 데이터가 틀릴 가능성을 반영하며, 0이면 한 번의 오답으로 목표 상품이 영원히 탈락한다.
- **추정 선호 (Phase 3+):** LLM이 매긴 확신도 c를 그대로 쓴다. 직접 답한 조건과 같은 식이라 별도 코드가 없다. c가 1/k보다 낮으면 1/k로 올린다. 그보다 낮으면 일치하는 상품이 오히려 불리해지기 때문이다.
- **unknown 상품:** 어떤 답에도 똑같이 반응한다. 그래서 그 속성의 답으로는 확률이 오르지도 내리지도 않는다. 속성 정보가 없는 상품을 함부로 탈락시키지 않는다.
- **"상관없음":** 상품과 무관한 답이라 belief를 바꾸지 않는다. 그 속성은 다시 묻지 않는다.
- **belief 계산:** belief = 사전분포 × Π 가능도를 정규화한 값이다([`compute_belief`](../backend/app/core/belief_tracker.py#L41 "docid:core.compute-belief")). 관측 순서와 무관하다.

## 2. 대화 상태

[`PreferenceState`](../backend/app/core/model.py#L45 "docid:core.preference-state")가 지금까지 알게 된 것을 담는다. 세션에 JSON으로 저장한다.

```json
{
  "explicit": {"connection": "wireless"},
  "inferred": {"anc": {"value": "yes", "confidence": 0.8, "reason": "지하철 통학"}},
  "dont_care": ["brand"],
  "asked": ["connection", "use_case"]
}
```

- **belief는 저장하지 않는다.** 매 턴 이 상태에서 다시 계산한다. 상품 150개 × 속성 8개라 계산이 매우 가볍다.
- **정정:** 같은 속성에 다시 답하면 이전 값을 바꾸고, 그 상태로 belief를 다시 계산한다. 이전 조건이 누적되지 않는다.
- **우선순위:** 직접 답 > 추정이다.
  - 직접 답하면 같은 속성의 추정은 지운다.
  - 직접 답했거나 "상관없음"이라고 한 속성은 추정하지 않는다.
- **질문 예산:** `asked`(엔진이 물은 속성)로 센다. 사용자가 먼저 말한 조건은 예산을 쓰지 않는다. "상관없음"이라는 답도 질문 하나를 쓴 것으로 센다.

## 3. 질문 선택

**기대 정보량** ([`expected_information_gain`](../backend/app/core/question_selector.py#L25 "docid:core.expected-ig"))
- 속성 a를 물었을 때 목표 상품에 대해 얻을 상호정보량이다.
  - I(T; A) = Σ_p Σ_v P(p)·L(v|p)·log₂(L(v|p) / P(v)),  P(v) = Σ_p P(p)·L(v|p)
- 답변은 1절과 같은 모델(확신도 1 − ε)로 생성된다고 본다.
- 노이즈가 없고 값이 고르게 나뉘면 log₂k bits다. 속성 값이 같거나 unknown뿐인 후보들 사이에서는 0이다.

**질문 후보** ([`candidate_questions`](../backend/app/core/question_selector.py#L50 "sym:py:app.core.question_selector#candidate_questions"))
- `askable`이고, 아직 답하지 않았고, 아직 묻지 않은 속성이다.

**정책** (평가에서 이것만 바꿔 비교한다)

| 정책 | 선택 |
|---|---|
| [`InformationGainPolicy`](../backend/app/core/question_selector.py#L73 "sym:py:app.core.question_selector#InformationGainPolicy") | 기대 정보량 최대 |
| [`RandomPolicy`](../backend/app/core/question_selector.py#L82 "sym:py:app.core.question_selector#RandomPolicy") | 무작위 (seed 고정 가능) |
| [`FixedOrderPolicy`](../backend/app/core/question_selector.py#L95 "sym:py:app.core.question_selector#FixedOrderPolicy") | 정해진 순서. 기본은 YAML의 속성 순서 |

**추정 선호가 질문 순서에 주는 영향**

추정은 belief만 바꾸고, IG는 바뀐 belief로 다시 계산된다. 방향은 추정 전에 belief가 어떻게 나뉘어 있었는지에 따라 다르다.
- **원래 반반이던 속성** (예: 연결 방식): 추정하면 한쪽으로 쏠려 그 질문의 IG가 **내려간다.** 이미 거의 아는 것은 덜 묻는다.
- **드문 값의 속성** (예: ANC가 10%뿐): 추정하면 yes와 no가 반반에 가까워져 IG가 **올라간다.** 엔진이 "통학용이면 노이즈 캔슬링이 필요할 것 같은데, 맞나요?" 같은 확인 질문을 저절로 앞에 둔다.

두 경우 모두 테스트로 확인했다([test_core.py](../backend/tests/test_core.py)).

## 4. 추천과 한계 표시

[`IGEngine.step`](../backend/app/core/engine.py#L120 "docid:core.engine-step")은 매 턴 질문(`Ask`)과 추천(`Recommend`) 중 하나를 돌려준다. 상태는 바꾸지 않는다.

**종료 조건** ([`EngineConfig`](../backend/app/core/engine.py#L23 "sym:py:app.core.engine#EngineConfig"))

| reason | 조건 | 기본값 |
|---|---|---|
| `budget` | 엔진이 물은 질문 수 ≥ 예산 | 4 |
| `confident` | Top-k 확률 합 ≥ 임계값 | k = 3, 0.9 |
| `no_informative_question` | 남은 질문의 기대 정보량이 모두 임계값 미만. 수집 가능한 속성으로는 후보를 더 구별할 수 없음 | 0.05 bits |

**추천** ([`recommend`](../backend/app/core/recommender.py#L50 "docid:core.recommend"))
- **정렬:** 확률 내림차순이다. 동률이면 인기도 점수 순이다.
  - 인기도 점수는 평점의 베이지안 평균이다([`popularity_scores`](../backend/app/core/adapters.py#L16 "sym:py:app.core.adapters#popularity_scores")). 확률 계산에는 쓰지 않는다.
- **조건 위반 처리:** 사용자가 직접 말한 조건을 어기는 상품은 뺀다(Safety: 요구와 무관한 추천 방지). 그런 상품을 빼면 k개가 안 될 때만 뒤에 채우고 `violated`로 표시한다.
- **추정 조건:** 추정 조건으로는 빼지 않는다. 확신이 낮은 추정이 하드 필터가 되면 안 되기 때문이다.
- **추천마다 붙는 정보:** `matched`(맞음), `unknown`(데이터에 없어 확인 못 함), `violated`(어긋남)를 붙인다. 대화 관리자가 이를 추천 근거 문장으로 바꾼다.

**추천 후 조정** ([`IGEngine.recommend_now`](../backend/app/core/engine.py#L100 "docid:core.engine-recommend-now"))
- 질문 없이 지금 상태로 바로 추천한다(reason `refined`).
- `PreferenceState.excluded`의 상품은 사전확률을 0으로 둔다. 모두 제외되면 제외를 무시한다.
- `tie_key`로 확률이 같은 상품 사이의 순서를 바꾼다(예: 가격 오름차순). 확률 계산 자체는 바꾸지 않는다.
- 사용 방법: [LLM 파서 6절](llm-parser.md#6-멀티턴-추천-조정)

**한계** ([`Limitation`](../backend/app/core/engine.py#L32 "sym:py:app.core.engine#Limitation")): 대화 관리자가 문장으로 바꿔 사용자에게 알린다.

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
