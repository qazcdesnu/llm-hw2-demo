# Done

← [CLAUDE.md](../CLAUDE.md) · 관련: [roadmap](roadmap.md) · [ongoing](ongoing.md)

끝난 작업 기록 (최신이 위).

| 완료일 | 작업 | Phase | 결과 / 산출물 |
|---|---|---|---|
| 2026-10-26 | 배포·데이터 준비 정리 | 0 | 단일 서버 배포(FastAPI가 `frontend/dist`를 함께 서비스, 테스트 2개, 실제 기동 확인), [prepare_data.sh](../backend/scripts/prepare_data.sh)(다운로드 크기 검증·이어받기 → 필터 → 빌드, 실제 실행 결과 카탈로그 동일). README에 로컬 실행·데이터 준비·배포·환경 변수·문제 해결 추가 |
| 2026-10-26 | 루트 README, 원본 없이 카탈로그 적재 | 0 | [README.md](../README.md): 소개, 빠른 시작, 구조, 결과, 한계. 빠른 시작을 위해 [load_catalog.py](../backend/scripts/load_catalog.py) 추가(git의 카탈로그 JSON → SQLite, 테스트 1개) |
| 2026-10-26 | LLM 파서 연결, 멀티턴 추천 조정, Safety | 3 | [`parse_message`](../backend/app/services/parser.py#L178 "docid:chat.parse-message"): 카테고리 정의로 만든 구조화 출력(요청 분류, 조건, 제외, 가격 정렬). 평가 세트 42개([parser_cases.yaml](../backend/evaluation/parser_cases.yaml), [eval_parser.py](../backend/scripts/eval_parser.py))로 모델 비교 → `gpt-5-mini`+`low`. 추천 후 조건 추가·변경·제외·가격순 재추천, 위험 요청 거절, 추정 확신도 구간 처리. 실제 서버+LLM으로 멀티턴 시나리오 확인. 테스트 79개(실제 LLM 호출 없음). 문서 [llm-parser.md](llm-parser.md) |
| 2026-10-26 | 웹에서 IG 엔진 동작 (LLM 없이) | 3 | [`handle_message`](../backend/app/services/chat.py#L323 "docid:chat.handle-turn")가 엔진 호출. API에 `answer`(버튼 답)·`question`(보기)·`finished` 추가, 글 입력은 보기 문자열 매칭, 추천 근거·한계 문장, 카탈로그 없으면 503. 프론트: 보기 버튼, 추천 카드(이미지·가격·근거), Developer Panel(질문별 IG 막대, 엔트로피, 종료 이유, 선호). 실제 서버 + 카탈로그 150개로 대화 끝까지 확인. 테스트 70개 |
| 2026-10-26 | **Phase 2 완료: IG 엔진** | 2 | `app/core/`: 가능도 모델(명시·추정·unknown·상관없음 통합), belief 계산, 기대 정보량(상호정보량), IG/Random/Fixed 정책, Top-k 추천과 조건 위반 처리, 종료 조건 3종과 한계 표시, 카탈로그 어댑터. 테스트 21개. [ig_demo.py](../backend/scripts/ig_demo.py). 예비 확인: 질문 4회 Hit@3 IG 0.63 / Fixed 0.46 / Random 0.32. 문서 [ig-engine.md](ig-engine.md). 추정의 IG 영향 설명을 바로잡음(드문 값은 IG 상승) |
| 2026-10-26 | 문서 ↔ 코드 링크 체계 | 0 | 심볼 ID 기반 링크(`sym:`/`docid:`)와 코드 태그(`@docid`/`@see`), AST 인덱서(Python `ast`, TS 컴파일러 API), `check`/`sync`/`index` 도구 [doclinks.py](../tools/doclinks/doclinks.py). 줄 이동·모듈 이동·이름 변경 추적 테스트 9개, 저장소 링크 검사를 pytest에 포함. 규칙 [doc-links.md](doc-links.md) |
| 2026-10-26 | 시스템 아키텍처 문서, 문서 간 링크 체계 | 0 | [architecture.md](architecture.md). 루트 [CLAUDE.md](../CLAUDE.md)의 문서 지도와 상대 링크·앵커로 문서 연결 |
| 2026-10-26 | 헤드폰 카탈로그 150개 샘플링 | 1 | 실제 분포 유지(연결 방식 × 착용 방식 비례 층화, seed 42). 후보 4,628개 대비 분포 차이 1~4%p. ANC 규칙 정밀도 문제 발견 → 애매한 "noise cancelling" 언급은 `unknown_if`로 unknown 처리. SQLite 저장, [headphones.json](../backend/data/catalog/headphones.json), [수동 검증 CSV](validation/headphones_validation.csv) 40개 |
| 2026-10-26 | 카테고리 확장 구조 설계 | 1 | 카테고리 정의 YAML(`backend/categories/`) + 스키마 검증·규칙 기반 추출·SQLite 저장(`app/catalog/`), 범용 스크립트 `filter_category.py`, `build_catalog.py`. 헤드폰 속성 8개 정의, 후보 4,909개(중복 제거 후)에 dry-run. 테스트 11개 추가. 가이드 [category-spec.md](category-spec.md) |
| 2026-10-26 | 헤드폰 데이터 필터링·분석 | 1 | 160만 개 중 헤드폰 56,048개, 후보 집합(가격 + 리뷰 100개 이상) 5,099개. 결과 [data-analysis.md](data-analysis.md) |
| 2026-10-26 | meta_Electronics.jsonl 다운로드 | 1 | `backend/data/raw/`, 5.25GB. 한 번 끊겨 이어받음. 크기 일치, 1,610,012줄 전부 JSON 파싱 확인 |
| 2026-10-26 | OpenAI LLM 클라이언트 | 3 | `app/llm/`: `LLMClient` 인터페이스 + `OpenAIClient`(Responses API, `complete`/`parse`), `backend/.env`에서 키 로드, 토큰 사용량 로깅, 테스트 6개. 실제 호출 확인용 [llm_smoke.py](../backend/scripts/llm_smoke.py) |
| 2026-10-26 | 프로젝트 기본 구조 | 0 | backend: uv + FastAPI + SQLite(`sessions`, `messages`), 세션 API 3개, pytest 4개 통과 / frontend: Vite 7 + React 19 + TS, 채팅 UI·Developer Panel, `/api` 프록시. 대화 처리(`app/services/chat.py`)는 stub |
| 2026-10-26 | plan.pdf 검토, 기술 결정 | 0 | Vite + FastAPI + SQLite, Safety 범위 확정 ([roadmap.md](roadmap.md#확정된-결정사항)) |
| 2026-10-26 | 문서 체계 구성 | 0 | [roadmap.md](roadmap.md), [ongoing.md](ongoing.md), [done.md](done.md), [issue.md](issue.md) |
