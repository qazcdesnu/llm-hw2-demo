# Backend

← [CLAUDE.md](../CLAUDE.md) · 전체 구조: [architecture.md](../docs/architecture.md)

FastAPI + SQLite. Python 3.12, 패키지 관리는 uv.

```bash
uv sync
uv run uvicorn app.main:app --reload   # http://127.0.0.1:8000, 문서: /docs
uv run pytest
```

## 환경 변수
`.env.example`을 `.env`로 복사하고 `OPENAI_API_KEY`를 넣는다. 키가 없어도 서버는 뜨고, LLM을 호출할 때만 에러가 난다.

```bash
uv run python scripts/llm_smoke.py   # 실제 API 호출 확인 (텍스트 1회 + 구조화 응답 1회)
uv run python scripts/eval_parser.py --verbose   # 파서 평가 세트 채점 (실제 API, 약 $0.04)
```

## 구조
- `app/main.py`: 앱 생성, 시작 시 SQLite 스키마 초기화
- `app/db.py`: SQLite 연결과 스키마 (`sessions`, `messages`, `categories`, `products`, `product_attributes`)
- `app/api/sessions.py`: 세션·대화 API
- `app/services/chat.py`: 대화 한 턴 처리 [`handle_message`](app/services/chat.py "docid:chat.handle-turn") (입력 해석 → IG 엔진 결정 → 질문·추천 응답, 추천 후 조정)
- `app/llm/`: LLM 클라이언트. [`LLMClient`](app/llm/base.py "docid:llm.client") 인터페이스(`complete`, `parse`)와 OpenAI 구현, [`get_llm_client`](app/llm/__init__.py "sym:py:app.llm#get_llm_client")
- `app/catalog/`: 카테고리 정의 스키마, 속성 추출, 카탈로그 DB 저장·조회
- `categories/`: 카테고리 정의 YAML (작성법: [category-spec.md](../docs/category-spec.md))
- `app/core/`: IG 엔진 (웹·DB와 무관한 순수 Python, 평가 코드에서도 사용). 진입점 [`IGEngine`](app/core/engine.py "docid:core.engine"), 설명 [ig-engine.md](../docs/ig-engine.md)
- `scripts/`: 데이터 스크립트 (`filter_category.py` → `build_catalog.py` → `export_validation.py`, 원본 없이 적재 `load_catalog.py`, 평가 데이터 `download_amazon_c4.py`, 분석 `analyze_headphones.py`, 엔진 데모 `ig_demo.py`)
- `data/catalog/<id>.json`: 빌드된 카탈로그 (git 포함)
- `data/raw/`: 외부 원본 데이터 (git 제외). Amazon Reviews 2023 메타(`meta_*.jsonl`), Amazon-C4 평가 질의(`amazon-c4/`)

## API
| Method | Path | 설명 |
|---|---|---|
| POST | `/api/sessions` | 새 세션 → `{session_id}` |
| GET | `/api/sessions/{id}` | 대화 기록, 대기 중인 질문(`question`), `finished` |
| POST | `/api/sessions/{id}/messages` | `{message, answer?}` → `{reply, question, recommendations, debug, finished}` |

- `answer`: 보기 버튼으로 고른 답 `{attribute, value}`. 없으면 `message`를 LLM 파서로 해석한다(키가 없거나 실패하면 보기 문자열 매칭).
- 추천 후(`finished: true`)에도 메시지를 보낼 수 있고, 추천 목록 조정으로 처리한다.
- `question`: 다음 질문과 보기(`options`). 마지막 보기는 항상 `dont_care`(상관없음)다. 추천으로 끝나면 `null`.
- 카탈로그가 DB에 없으면 503이다(`scripts/build_catalog.py <id>` 먼저 실행). 정의에 없는 답이면 422다.
- 새 세션의 카테고리는 설정 `DEFAULT_CATEGORY`(기본 `headphones`)다.
| GET | `/api/health` | 상태 확인 |
