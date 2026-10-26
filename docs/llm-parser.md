# LLM 파서와 멀티턴 대화

← [CLAUDE.md](../CLAUDE.md) · 관련: [architecture 3절](architecture.md#3-대화-한-턴의-흐름) · [IG 엔진](ig-engine.md) · [roadmap Phase 3+](roadmap.md#phase-3-기능-고도화-암묵적-선호-추론과-추천-보정)

사용자의 자유 입력을 카테고리 속성 조건과 요청 분류로 바꾸는 파서([`parse_message`](../backend/app/services/parser.py#L178 "docid:chat.parse-message")).
그리고 그 결과로 대화 상태를 바꾸는 대화 관리자([`handle_message`](../backend/app/services/chat.py#L323 "docid:chat.handle-turn"))를 설명한다.

**원칙: LLM은 해석만 한다.** 무엇을 묻고 무엇을 추천할지는 IG 엔진이 상품 데이터로 정한다. LLM은 상품 속성을 만들지 않는다.

## 1. 입력 해석 순서

| 입력 | 처리 | LLM 호출 |
|---|---|---|
| 보기 버튼 | `answer {attribute, value}`를 그대로 반영 | 없음 |
| 자유 입력 | LLM 파서 | 1회 |
| 자유 입력 + 파서 없음(키 미설정) 또는 실패 | 대기 중인 질문의 보기 이름과 문자열 매칭([`match_text`](../backend/app/services/chat.py#L123 "sym:py:app.services.chat#match_text")) | 없음 |

## 2. 파서

### 입력
- **시스템 지시** ([`build_instructions`](../backend/app/services/parser.py#L81 "docid:chat.parser-instructions")): 카테고리 정의(YAML)의 속성·값·라벨로 매번 만든다. 카테고리별 코드는 없다.
- **사용자 입력:** 발화와 함께 맥락을 넘긴다.
  - 현재 질문(있으면)
  - 지금까지 명시된 조건
  - 현재 추천 목록 요약(추천 후일 때): 상품명 | 가격 | 속성들

### 출력 (구조화 출력, [`schema_for`](../backend/app/services/parser.py#L74 "sym:py:app.services.parser#schema_for"))

| 필드 | 내용 |
|---|---|
| `request_type` | `shopping` / `other_product` / `off_topic` / `unsafe` |
| `constraints[]` | `{attribute(enum), value, kind: explicit\|inferred, confidence, reason}`. 이번 발화에서 새로 말하거나 바뀐 것만 |
| `exclude_ranks[]` | "1번 말고" → 현재 추천에서 뺄 순위 |
| `price_order` | "더 싼 걸로" → `cheaper`, "더 비싸도 좋은 걸로" → `pricier`, 그 외 `none` |

- **속성 이름:** enum이라 정의에 없는 속성은 나올 수 없다.
- **값:** 코드에서 정의와 대조하고, 맞지 않으면 버린다([`validate_constraints`](../backend/app/services/parser.py#L156 "sym:py:app.services.parser#validate_constraints")).
- **자유값 속성(brand):** 영문 소문자로 받는다(소니 → `sony`).
- **명시 조건의 confidence:** 1로 고정한다. LLM이 매긴 값은 추정에만 쓴다.

### 구조를 바꾼 이유 (처음 버전의 문제)
- **처음 버전:** 속성마다 칸이 있는 형태(`{connection: …|null, anc: …|null, …}`)였다.
- **문제:** 모델이 말하지 않은 속성까지 `dont_care`로 채웠다. "무선이고 노캔"에 6개 속성이 상관없음으로 채워져, 엔진이 더 묻지 못했다.
- **해결:** "언급한 조건만 담는 목록"으로 바꾸고 규칙과 예시를 보강해서 해결했다.

## 3. 평가

[eval_parser.py](../backend/scripts/eval_parser.py)가 [parser_cases.yaml](../backend/evaluation/parser_cases.yaml) 42개 케이스를 실제 API로 채점한다.
- **케이스 구성:** 명시 조건 여러 개, 가격 환산, 추정, 부정·상관없음, 짧은 답, 정정, 다른 상품, 잡담, 위험 요청, 맥락이 있는 대화
- **통과 조건:** 요청 분류가 맞고, 기대한 명시 조건이 모두 있으며, 기대 밖의 명시 조건이 없어야 한다.

```bash
cd backend
uv run python scripts/eval_parser.py --configs gpt-5-mini:low gpt-5-nano:low --verbose
```

**모델 선택 (2026-10-26)**

| 설정 | 통과 (3회) | 위험 요청 | 추정 맞힘 | 응답 시간 | 1천 턴당 비용 |
|---|---|---|---|---|---|
| **gpt-5-mini + low (기본값)** | **42, 42, 42 / 42** | 5/5 | 5/5 | 3.1초 | $0.89 |
| gpt-5-nano + low | 39, 37, 36 / 42 | 4/5 | 1~4/5 | 3.0초 | $0.22 |
| gpt-5-nano + medium | 41/42 | 5/5 | 3/5 | 9.5초 | $0.63 |
| gpt-5-nano + minimal | 29/35 (맥락 케이스 추가 전) | — | 0/5 | 1.4초 | $0.10 |
| gpt-4.1-nano | 호출 불가 (OpenAI에서 폐기, 404) | | | | |

- **gpt-5-nano + low를 쓰지 않은 이유:** 대화 맥락이 함께 있을 때 "총 구하는 방법"을 매번 쇼핑으로 분류했다(Safety 실패). 결과도 실행마다 흔들렸다.
- **gpt-5-mini 비용:** 1천 턴에 약 $0.89라 $20 예산으로 약 2만 턴을 쓸 수 있다.
- **설정 위치:** `backend/.env`의 `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`

## 4. 해석 결과를 대화 상태에 반영
([`apply_parsed`](../backend/app/services/chat.py#L153 "docid:chat.apply-parsed"))

| 출력 | 처리 |
|---|---|
| explicit | `PreferenceState.answer`: 같은 속성의 이전 값·추정을 대체한다(정정) |
| inferred, confidence ≥ 0.85 | 명시 조건으로 올린다 |
| inferred, 0.5 ≤ confidence < 0.85 | `PreferenceState.infer`: 약한 증거로 belief에만 반영하고, 조건 위반으로 빼지는 않는다 |
| inferred, confidence < 0.5 | 버린다 |
| `exclude_ranks` | 해당 상품을 후보에서 제외한다(`PreferenceState.excluded`) |
| `price_order` | 대화 상태에 저장한다. 조건이 똑같이 맞는 상품 사이를 가격순으로 정렬한다 |

- **알림 문구:** 실제로 바뀐 것만 "반영했어요: …"로 알린다. 이미 있는 조건을 다시 말한 것은 알리지 않는다.
- **질문에 답하지 않고 다른 정보만 준 경우:** 그 질문을 "안 물은 것"으로 되돌린다. 엔진이 필요하면 다시 묻는다.
- **확인 질문:** 추정이 있으면 엔진이 그 속성을 확인 질문처럼 먼저 묻기도 한다. 드문 값을 추정하면 IG가 올라가기 때문이다([IG 엔진 3절](ig-engine.md#3-질문-선택)). 실제 대화에서 "지하철 출퇴근 → ANC 추정 → ANC 질문"이 이렇게 나왔다.

## 5. 요청 분류와 Safety

| request_type | 응답 | 상태 |
|---|---|---|
| `shopping` | 조건 반영 후 질문 또는 추천 | 변경 |
| `unsafe` | "그 요청은 도와드릴 수 없어요" 후 대기 중인 질문을 다시 보여준다 | 변경 없음 |
| `other_product` | "지금은 헤드폰·이어폰만 추천할 수 있어요" | 변경 없음 |
| `off_topic` | 서비스 범위 안내 | 변경 없음 |

- **분류 기준:** 요청 분류는 맥락이 아니라 발화 자체로 판단하라고 지시한다. 지시가 없으면 맥락에 끌려 쇼핑으로 분류하는 경우가 있었다.
- **위험 단어가 있는 정상 쇼핑:** "사격장에서 쓸 헤드폰", "총 게임용 헤드셋"은 쇼핑으로 분류한다(평가 세트에 포함).
- **요구와 무관한 추천 방지:** 이 Safety 항목은 엔진이 담당한다. 명시 조건을 어기는 상품은 빼고, 모자라면 알린다([IG 엔진 4절](ig-engine.md#4-추천과-한계-표시)).

## 6. 멀티턴 추천 조정

추천을 보여준 뒤에도 대화가 이어진다(`ChatState.finished`는 "추천을 한 번 이상 보여줌"이라는 뜻).
이후 자유 입력은 추천 목록 조정으로 처리하고, 질문 없이 바로 재추천한다([`IGEngine.recommend_now`](../backend/app/core/engine.py#L100 "docid:core.engine-recommend-now")).

| 사용자 | 처리 |
|---|---|
| "노캔 있는 걸로", "유선으로 바꿔줘" | 조건 추가·변경 → 재추천 |
| "더 저렴한 걸로" | 조건이 같은 상품 사이를 가격 오름차순으로 정렬. 안내 문구도 "가격이 낮은 순으로 골랐어요"로 바뀐다 |
| "1번 말고" | 해당 상품 제외 |
| 조건을 모두 만족하는 상품이 없음 | "일부 조건이 다른 상품도 함께 보여드려요" + 근거에 "(요청하신 조건과 다름)" |
| 알아들을 수 없음 | "어떤 점을 바꾸고 싶으신지 조금만 더 알려주세요" |

"더 싼 걸로"를 LLM이 가격대로 바꾸게 하지 않은 이유:
- 현재 추천의 가격대가 섞여 있으면($20, $35, $84) "한 단계 낮은 가격대"가 정해지지 않는다. 실제로 아무 효과가 없었다.
- 그래서 LLM은 방향만 정하고, 정렬은 코드가 결정적으로 한다.

프론트엔드는 추천 아래에 조정 예시 버튼("더 저렴한 걸로", "노이즈 캔슬링 있는 걸로", "1번 말고 다른 걸로")을 보여준다.

## 알려진 한계

모름·용어 되묻기·"일단 목록 보기"·숫자 가격 범위를 처리하지 못한다. 분석과 해결 계획은 [roadmap Phase 3++](roadmap.md#phase-3-대화-견고성-모름되묻기바로-보기숫자-가격)에 있다([I-10](issue.md#i-10)).

## 7. 테스트

- **대화 테스트:** [test_sessions.py](../backend/tests/test_sessions.py)는 가짜 파서를 주입해 네트워크 없이 확인한다.
  - 확인 항목: 여러 조건 한 번에, 추정 확신도 구간, 위험·다른 상품 요청, 답 안 한 질문 되돌리기, 추천 후 조건 변경·제외·가격 정렬, 같은 조건 재언급
- **실제 LLM을 막는 장치:** `.env`에 키가 있어도 테스트는 실제 LLM을 부르지 않는다. `get_parser` 의존성을 테스트에서 덮어쓴다.
- **해석 품질:** 실제 API 평가는 3절의 평가 스크립트로 따로 돌린다.
