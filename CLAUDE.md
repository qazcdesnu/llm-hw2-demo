# RecommandShopping

IG 기반 대화형 상품 추천 챗봇 (HW2). Frontend `frontend/` (Vite), Backend `backend/` (FastAPI + SQLite).
원본 계획은 [docs/plan.pdf](docs/plan.pdf).

## 문서 지도
이 파일이 모든 문서의 루트다. 각 문서는 맨 위에 이 파일로 돌아오는 링크를 둔다.

| 문서 | 내용 |
|---|---|
| [README.md](README.md) | 프로젝트 소개, 빠른 시작, 구조 요약, 현재 결과 (사람용 첫 페이지) |
| [architecture.md](docs/architecture.md) | 시스템 구조, 대화 한 턴의 흐름, 데이터 파이프라인, DB 모델, 설계 원칙 |
| [ig-engine.md](docs/ig-engine.md) | IG 엔진: 가능도 모델, 대화 상태, 질문 선택, 종료 조건·한계 표시, 예비 결과 |
| [llm-parser.md](docs/llm-parser.md) | LLM 파서(요청 분류·조건 추출), 멀티턴 추천 조정, Safety, 파서 평가와 모델 선택 |
| [evaluation.md](docs/evaluation.md) | 평가: C4 시나리오, 가상 사용자·LLM 심판, 비교군(Random, LLM), 평가 모드, 결과 |
| [roadmap.md](docs/roadmap.md) | 전체 단계(Phase)와 확정된 결정사항 |
| [ongoing.md](docs/ongoing.md) | 진행 중인 작업 |
| [done.md](docs/done.md) | 완료 기록 |
| [issue.md](docs/issue.md) | 문제점·리스크·미결정 사항 (I-번호) |
| [doc-links.md](docs/doc-links.md) | 문서 ↔ 코드 링크 형식(`sym:`, `docid:`, `@see`)과 `doclinks` 도구 |
| [category-spec.md](docs/category-spec.md) | 카테고리 정의 YAML 형식과 새 카테고리 추가 절차 |
| [data-analysis.md](docs/data-analysis.md) | 헤드폰 데이터 분석 결과 (속성 선정 근거) |
| [validation/](docs/validation/headphones_validation.csv) | 카탈로그 수동 검증 CSV |
| [backend/README.md](backend/README.md) | 백엔드 실행, 환경 변수, 코드 구조, API |

## 문서 작성 규칙
- 다른 문서·코드를 언급할 때는 Markdown 상대 링크를 쓴다: `[표시 이름](상대/경로.md)`
- 특정 절은 앵커로 가리킨다: `[roadmap Phase 2](docs/roadmap.md#phase-2-information-gain-엔진)`
  - 앵커는 GitHub 규칙을 따른다: 소문자로 바꾸고, 공백은 `-`로, 그 밖의 문장부호는 제거
- 이슈는 `[I-7](docs/issue.md#i-7)`처럼 링크한다. `issue.md`의 각 ID 칸에 `<a id="i-7"></a>` 앵커가 있다.
- 새 문서를 만들면 위 문서 지도에 추가하고, 문서 맨 위에 `← [CLAUDE.md](../CLAUDE.md)`를 둔다.
- 코드는 줄 번호(`#L10`)로 링크하지 않고 객체·함수·클래스 단위로 링크한다: `[이름](경로 "sym:py:모듈#이름")` 또는 `"docid:…"`. 주소에는 파일 경로만 둔다 ([doc-links.md](docs/doc-links.md))
- 문서 → 코드 링크가 기준이고 코드의 `@see`는 그 역참조다. `@see`는 손으로 쓰지 않고 `uv run --project backend python tools/doclinks/doclinks.py sync`로 생성한다. 양방향이 어긋나면 `pytest`가 실패한다.
- Claude Code hook([.claude/settings.json](.claude/settings.json))이 파일 수정 직후 링크 오류와 "방금 고친 코드를 참조하는 문서"를 알려 준다. 알려 준 문서의 설명이 코드와 맞는지 확인하고 함께 고친다. 오류가 남으면 종료 전 hook이 막는다.

## 실행
- 처음 한 번: `cd backend && uv sync && uv run python scripts/load_catalog.py headphones` (git의 카탈로그 JSON → SQLite)
- backend: `cd backend && uv run uvicorn app.main:app --reload` (8000), 테스트 `uv run pytest`
- frontend: `cd frontend && npm run dev` (5173, `/api`는 8000으로 프록시). Vite는 `localhost`(IPv6)에만 listen하므로 `127.0.0.1:5173`으로는 접속되지 않음
- 자세한 내용: [backend/README.md](backend/README.md)

## 작업 관리
아래 네 문서로 할 일을 관리한다. 작업을 시작하거나 마칠 때 반드시 갱신한다.
- [roadmap.md](docs/roadmap.md): 전체 단계와 확정된 결정사항. 결정이 바뀌면 여기에 반영
- [ongoing.md](docs/ongoing.md): 진행 중인 작업. 끝나면 [done.md](docs/done.md)로 옮김
- [done.md](docs/done.md): 완료 기록 (날짜, 산출물)
- [issue.md](docs/issue.md): 문제점·리스크·미결정 사항. 해결되면 상태와 결정 내용 기록

구조가 바뀌면 [architecture.md](docs/architecture.md)의 해당 절과 구성 요소 상태(✅/🚧/📝)도 갱신한다.

## 데이터
- 원본: `backend/data/raw/` (git 제외, 대용량)
- 카테고리는 `backend/categories/<id>.yaml`로 정의하고, 카테고리별 코드는 만들지 않는다 ([category-spec.md](docs/category-spec.md))
- 파이프라인: `scripts/filter_category.py <id>` → `data/interim/<id>.jsonl` → `scripts/build_catalog.py <id>` → SQLite + `data/catalog/<id>.json` ([architecture 4절](docs/architecture.md#4-데이터-파이프라인-오프라인))
