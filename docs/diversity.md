# 추천 집중도와 속성 일치 분석

← [CLAUDE.md](../CLAUDE.md) · 관련: [evaluation](evaluation.md) · [IG 엔진](ig-engine.md) · [I-4](issue.md#i-4)

IG 질문과 동률 정렬이 서로 다른 사용자에게 같은 상품을 반복 추천하는지("echo chamber" 우려)를 잰다. 추천이 정답 상품의 속성과 얼마나 같은지도 우연 수준과 비교해 잰다.
새 대화는 돌리지 않는다. **이미 저장된 평가 run의 추천 목록(`eval_episodes`)만** 읽는다. LLM 호출은 없고 DB는 읽기 전용으로 연다.

## 1. 무엇을 재는가

추천 연구에서 "echo chamber"(Jiang et al. 2019)는 같은 아이템에 반복 노출돼 관심이 강화되는 **시간적 피드백 루프**를 말한다. 우리 챗봇은 대화 한 번으로 끝나고 다시 학습하지 않으므로, 이 루프는 생기지 않는다. 대신 아래 세 가지를 잰다.

| 개념 | 질문 | 지표 |
|---|---|---|
| Aggregate diversity (Adomavicius & Kwon 2012) | 전체 사용자에게 서로 다른 상품을 몇 개나 추천했나 | Coverage, distinct ratio, 누적 곡선 |
| 동질화 (Chaney et al. 2018) | 필요가 다른 사용자가 같은 상품을 받는가 | 사용자 쌍 Jaccard (필요 같음 / 다름) |
| 인기도 편향 (Lin et al. 2022, CRS) | 추천이 인기 상품에 쏠리는가 | 리뷰 수 백분위, Gini, 상위 점유율 |

## 2. 지표 정의

n = 에피소드(사용자) 수, k = 추천 개수(3), N = 카탈로그 크기(4,628).

**집중도** ([`diversity`](../backend/app/evaluation/diversity.py))

| 지표 | 함수 | 정의 | 읽는 법 |
|---|---|---|---|
| 노출 횟수 | [`exposure`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#exposure") | 상품별로 몇 개의 에피소드에서 추천됐나 (한 에피소드 안의 중복은 1회) | 다른 지표의 입력 |
| Coverage | [`coverage`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#coverage") | \|∪추천\| / N | 상한이 k·n / N이다(n=10이면 0.65%). 그래서 단독으로 쓰지 않는다 |
| Distinct ratio | [`distinct_ratio`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#distinct_ratio") | \|∪추천\| / 추천 칸 수 | 1이면 겹침이 없다. **중복을 보는 핵심 지표** |
| Gini | [`gini`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#gini") | 노출 횟수의 Gini 계수 | 0 = 고르게, 1에 가까울수록 쏠림. 추천된 상품 기준과 카탈로그 전체(0 포함) 기준 두 가지를 낸다. 카탈로그 기준은 n이 작으면 거의 1이다 |
| 유효 아이템 수 | [`effective_items`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#effective_items") | exp(노출 분포의 Shannon entropy) | 사실상 몇 개를 고르게 돌려 쓰는가 |
| 상위 점유율 | [`top_share`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#top_share") | 가장 많이 나온 10개가 차지하는 칸 비율 | |
| 누적 곡선 | [`accumulation_curve`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#accumulation_curve") | 에피소드를 하나씩 더할 때의 고유 상품 수 | 기울기가 k보다 작아지면 중복이 생긴 것이다. 정답 상품 곡선과 비교한다 |
| 반복 추천 | [`repeated_items`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#repeated_items") | 둘 이상의 에피소드에 나온 상품과 그 위치 | 질적 분석용 |
| 사용자 쌍 겹침 | [`pairwise_overlap`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#pairwise_overlap") | 에피소드 쌍의 Jaccard 평균과 겹친 쌍 수를 **같은 시나리오(반복 실행) / 필요가 같은 쌍 / 필요가 다른 쌍**으로 나눔 | 필요가 같은데 겹치면 정당하다. 필요가 다른데 겹치면 동질화다 |
| 인기도 | [`popularity_percentiles`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#popularity_percentiles") | 리뷰 수(`rating_number`)의 카탈로그 백분위 | 카탈로그 중앙값은 0.5다. 정답 상품의 값과 비교한다 |

- "필요"(signature)는 정답 상품의 **조건 속성** 조합이다(시스템이 묻는 `askable` 속성, brand 제외. headphones에서는 anc, connection, form_factor, mic, price_band, water_resistant). 엔진은 이 속성으로 거르므로, 조합이 같으면 같은 후보 묶음에 도달한다.
- 정답 속성은 **지금 정의에 있는 속성만** 쓴다. 시나리오 파일에 남은 `use_case`(2026-11-03 정의에서 삭제)는 비교하지 않는다. [`attribute_match`](../backend/app/evaluation/runner.py "sym:py:app.evaluation.runner#attribute_match")와 같은 기준이라, run 8 이후는 1위 값이 저장된 값과 같다. run 3·5·7의 저장값은 당시 정의(주 용도 포함)로 계산돼 다르다.
- 여러 run을 합치면 에피소드 하나가 사용자 한 명(`run:시나리오`)이다. 같은 시나리오를 다시 실행한 쌍은 **같은 시나리오(반복)** 묶음으로 따로 센다. 이 묶음의 Jaccard는 같은 사용자에게 얼마나 일관되게 추천하는지를 뜻한다.
- 스크립트는 정답과 조건 속성 조합이 같은 카탈로그 상품 수(`constraint_cell_size`)도 남긴다. 텍스트 정렬이 없으면 이 묶음 안의 순서는 평점이다.

**속성 일치**

| 지표 | 함수 | 정의 |
|---|---|---|
| 속성 상태 | [`attribute_status`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#attribute_status") | 정답의 알려진 속성마다 같음 / 다름 / **확인 불가**(추천 상품의 값이 unknown) |
| 일치율 | [`match_rate`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#match_rate") | 정답의 알려진 속성 중 같은 비율. unknown은 다름으로 센다(기존 attribute_match와 같은 정의). `keys`로 일부 속성만 볼 수 있다 |
| 우연 기준선 p₀ | [`chance_match`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#chance_match") | 카탈로그 전체 상품과 정답의 평균 일치율. 표본을 뽑지 않고 전체로 정확히 계산한다 |
| Lift | [`lift`](../backend/app/evaluation/diversity.py "sym:py:app.evaluation.diversity#lift") | (일치 − p₀) / (1 − p₀). 0 = 무작위 상품 수준, 1 = 모두 일치 |

- 속성 분포가 한쪽으로 쏠려 있어(예: wireless가 대부분) 아무 상품이나 골라도 일치율이 0.4~0.5다. 그래서 일치율 0.73과 0.90을 비교할 때는 lift로 본다.
- 스크립트는 1위, Top-3 평균, Top-3 최대를 모두 낸다. 속성별 같음/다름/확인 불가 횟수도 1위 기준과 Top-3 기준으로 낸다.

## 3. 실행

```bash
cd backend
# 현재 버전(커밋 9e5a967)의 시나리오 44개 전부: run 13(기존 10개) + run 14(새 34개). 지금 csv/에 있는 결과
uv run python scripts/analyze_diversity.py headphones --pool 13 14
```

- 현재 성능은 **run 13 + run 14**(같은 코드 9e5a967, 시나리오 44개를 한 번씩)로 본다. run 8~12는 run마다 코드가 달라 섞지 않는다([README 평가 실행 기록](../README.md#평가-실행-기록-run별-코드-변경)).
- 실행할 때마다 출력 폴더의 이전 JSON·CSV를 지우고 이번 결과로 교체한다.

[analyze_diversity.py](../backend/scripts/analyze_diversity.py)
- [`load_context`](../backend/scripts/analyze_diversity.py "sym:py:scripts.analyze_diversity#load_context"): 속성 정의·카탈로그·시나리오를 읽고, 시나리오마다 정답 속성(지금 정의 기준), 필요(signature), 우연 기준선 p₀, 같은 조합의 카탈로그 상품 수를 미리 계산한다.
- [`analyze`](../backend/scripts/analyze_diversity.py "sym:py:scripts.analyze_diversity#analyze"): 에피소드 묶음(run 하나 또는 여러 run)의 지표를 낸다. 사용자 = `run:시나리오`. 오류 에피소드는 빼고, 시나리오 파일에 없는 시나리오가 있으면 그 묶음을 건너뛴다(run 2는 이전 시나리오라 분석할 수 없다).
- [`concentration`](../backend/scripts/analyze_diversity.py "sym:py:scripts.analyze_diversity#concentration"): 목록 묶음 하나의 집중도 지표. 시스템별, 실제 구매 상품(시나리오당 1개)에 적용한다.
- [`attribute_report`](../backend/scripts/analyze_diversity.py "sym:py:scripts.analyze_diversity#attribute_report"): 시스템 하나의 속성 일치(1위, Top-3 평균·최대, p₀, lift, 속성별 같음/다름/확인 불가).
- [`per_scenario`](../backend/scripts/analyze_diversity.py "sym:py:scripts.analyze_diversity#per_scenario"): 시스템 하나의 시나리오별 1위 속성 일치·lift·정답 순위 %, 같은 시나리오 반복의 Jaccard, 다른 시나리오의 에피소드와 겹친 수(필요 같음/다름).
- [`write_csvs`](../backend/scripts/analyze_diversity.py "sym:py:scripts.analyze_diversity#write_csvs"): 아래 CSV를 쓴다. DB는 읽기 전용으로 열고 LLM은 부르지 않는다.

**출력** (`backend/evaluation/diversity/`)

| 파일 | 내용 |
|---|---|
| `<group>.json` | 묶음(`pool12-13`, `run12` 등)별 전체 결과: `systems.<s>`(집중도, `curve`, 인기도, `pairwise`, `attribute_match`, 저장된 `stored_attribute_match`·`rank_pct_mean/median`), `target`(실제 구매 상품), `repeated_items`, `cross_system`, `scenarios` |
| `csv/<지표>.csv` | 지표 하나의 표. 행 = 묶음, 열 = ig·random·llm(·target). 지표 목록은 스크립트의 `METRICS` |
| `csv/summary.csv` | 한눈에 보는 표. 행 = 지표, 열 = 시스템(묶음이 여럿이면 `묶음:시스템`) |
| `csv/per_scenario.csv` | 묶음·시스템·시나리오별 지표 (`per_scenario`) |
| `csv/metrics_long.csv` | 모든 지표를 `group, system, metric, value` 한 줄씩 |
| `csv/per_attribute.csv` | 묶음·시스템·범위(top1/top3)·속성별 같음/다름/확인 불가 횟수 |
| `csv/repeated_items.csv` | 두 번 이상 추천된 상품: 횟수, 서로 다른 시나리오 수, 리뷰 수·인기도, 위치(`시스템:run:시나리오`) |
| `csv/cross_system.csv` | 시스템 쌍의 같은 에피소드 Jaccard 평균, 전체 합집합 Jaccard |

- 묶음 크기가 다르면 distinct ratio를 직접 비교하지 않는다(에피소드가 많을수록 낮아진다).

테스트: [test_diversity.py](../backend/tests/test_diversity.py) (Gini 극단값, distinct ratio, 누적 곡선, 같은 시나리오/필요 같음/다름 분해, unknown을 확인 불가로 세기, p₀·lift)

## 4. 결과 요약 (현재 버전, run 13·14, 시나리오 44개, 2026-11-04)

시나리오 44개 × 1회 = 시스템별 대화 44개, 추천 132건.

| | ig | random | llm | 실제 구매 |
|---|---|---|---|---|
| 고유 추천 비율 | 0.89 (117/132) | 0.85 (112/132) | 0.88 (116/132) | 0.91 (40/44) |
| 인기도 백분위 평균 | 0.44 | 0.45 | 0.51 | 0.80 |
| 1위 속성 일치도 | 0.94 | 0.94 | 0.81 | |
| 우연 기준 일치도 p₀ | 0.51 | 0.51 | 0.51 | |
| 보정 일치도 | **0.88** | 0.87 | 0.62 | |
| 모든 속성 일치 시나리오 (44개 중) | 33 | 32 | 13 | |

- **소수 상품 쏠림은 없다.** 여러 시나리오에 같은 상품이 간 경우는 정답 조건이 같거나, 대화에서 조건이 어긋났거나(c4-4751: 세 시스템 모두 고가·ANC 상품을 추천), 카탈로그 표기 문제(이어훅 이어폰 정답이 오버이어, I-9)인 경우였다.
- **인기도 편향은 반대 방향이다.** 추천은 카탈로그 중간쯤이고 실제 구매는 상위다.
- **속성 일치도는 ig와 random이 비슷하다.** 시나리오 10개(run 12·13)에서는 ig 0.92 / random 0.84였지만 44개로 넓히자 0.88 / 0.87이 됐다. llm은 가격대·착용 방식 불일치와 확인 불가(ANC·마이크 값 없음)가 많다.
- 각 시나리오를 한 번씩 돌린 결과라 검정하지 않았다.

자세한 표·그림·해석과 참고문헌은 [보고서 Artifact](https://claude.ai/artifact/YAsZEvo7rycWiqCf7UiogN)에 있다(비공개, 공유 메뉴로 팀에 공개).

## 5. 다음 실험 (설계만, 실행하지 않음)

| ID | 방법 | 규모 | 비용 |
|---|---|---|---|
| E1 | LLM 없이 구조화 사용자(정답 속성대로 답함, [ig_demo.py](../backend/scripts/ig_demo.py) `--target` 방식)로 ig/random/fixed를 돌린다. 사용자는 C4 정답 126개, 또는 카탈로그 4,628개 전체를 가상 정답으로 둔다. 텍스트 정렬은 끄거나 C4 질의 BM25로 켠다 | 126 / 4,628 | $0 |
| E3 | 시나리오 44개 × ig/random/llm × 반복 2회. 같은 시나리오 반복의 일관성(사용자 내)과 시나리오 간 중복(사용자 간)을 나눠 본다 | 264 대화 | 약 $7~8 |

기준선 두 가지를 추가한다. ① 정답 조건을 모두 만족하는 상품 중 무작위 3개: 같은 속성 일치에서 얻을 수 있는 다양성의 상한. ② 같은 조건에서 평점 Top-3: 하한, 순수 echo 상황.
