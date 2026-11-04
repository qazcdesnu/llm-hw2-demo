# RecommandShopping

Information Gain(IG)으로 질문을 골라 원하는 상품을 찾아 주는 대화형 쇼핑 추천 챗봇입니다. (GSDS LLM 과목 HW2)

> **📌 팀원은 [docs/briefing.md](docs/briefing.md)부터 읽어 주세요.** 과제의 현재 진행 상황을 한 문서에 정리했습니다.
> 핵심 로직, 아키텍처와 코드 지도, 평가 방법, 최신 평가 결과(run 7), 설계 결정과 근거, 남은 일과 역할 분담 후보, 실행·데모 방법이 들어 있습니다.

실제 Amazon 상품 데이터(Amazon Reviews 2023)로 카탈로그를 만들었습니다. 매 턴 **"이 질문에 답을 들으면 후보가 얼마나 줄어드는가"**(기대 정보량)를 계산해 가장 유익한 질문부터 묻습니다.
무엇을 묻고 무엇을 추천할지는 상품 데이터로 엔진이 정합니다. LLM은 자유 입력 해석(요청 분류·속성 추출), 질문 말투(친구 페르소나), 같은 조건의 상품을 대화와 가까운 순으로 정렬하는 임베딩에 씁니다.

> 문서 지도와 작업 규칙은 [CLAUDE.md](CLAUDE.md)에 있습니다.

## 할 수 있는 것

- **IG 기반 질문:** 질문마다 기대 정보량(bits)을 계산하고, 가장 큰 것을 묻습니다. 후보를 더 구별할 수 없으면 질문을 멈추고 그 한계를 알려줍니다.
- **자유 입력 해석:**
  - "무선이고 노캔 되는 걸로"처럼 여러 조건을 한 번에 말해도 됩니다.
  - 정정, "상관없어" 같은 답도 처리합니다.
  - "시끄러운 데서 방해받지 않고 듣고 싶어"처럼 바람·상황에서 그럴듯한 값은 짐작해 반영하고, "액티브 노이즈 캔슬링 있는 걸 원하는구나"처럼 짐작을 말해 줍니다. 아니라고 하면 바로 고칩니다. 질문과 무관한 말에서도 찾습니다.
- **친구 같은 질문:** 기능을 직접 묻지 않고, 쓰는 장면에서 무엇을 바라는지 물어 은근히 알아냅니다. 알게 된 것은 "그럼 ~가 좋겠네"처럼 말하고 다음 질문으로 넘어갑니다. 예산은 돌려 묻지 않고 가볍게 직접 묻습니다.
- **한국어·영어:** 사용자가 쓰는 언어로 답합니다(질문, 안내 문구, 보기, 추천 근거).
- **같은 조건의 상품 정렬:** 조건이 똑같이 맞는 상품이 많으면 대화 내용과 상품 설명이 가까운 것(키워드 검색 + 임베딩), 평점 순으로 보여 줍니다. 쓰임새("헬스장에서", "재택근무용")도 여기로 반영됩니다. 주 용도는 상품 메타데이터와 실제 사용 목적이 자주 달라 속성에서 뺐습니다.
- **멀티턴 추천 조정:** 추천을 받은 뒤 "더 저렴한 걸로", "1번 말고", "유선으로 바꿔줘"라고 하면 목록을 다시 골라 줍니다.
- **근거와 한계 표시:**
  - 추천마다 맞는 조건, 확인할 수 없는 조건, 요청과 다른 조건을 표시합니다.
  - 동률 후보가 많거나 조건을 모두 만족하는 상품이 부족하면 그렇다고 알려줍니다.
- **Safety:**
  - 위험한 요청(총기, 마약 등)은 거절합니다.
  - 사용자가 직접 말한 조건을 어기는 상품은 추천에서 뺍니다.
- **모델 선택:** 입력창 왼쪽에서 해석·질문 작성에 쓸 OpenAI 모델을 고릅니다. API 가격 페이지의 텍스트 모델 중 실제로 호출되고 reasoning effort `low`를 받는 19개를 가격과 함께 보여 줍니다(기본 `gpt-5-mini`).
- **Developer Panel:** 질문별 기대 IG, 엔트로피, 상위 후보 확률, 대화 메모리(값과 근거 발화)·질문 기록, 질문 작성기의 접근, 요청 분류, 동률 정렬 기준을 실시간으로 보여줍니다.
- **평가 모드:** Developer Panel에서 전환합니다. Amazon-C4의 실제 구매자 시나리오를 직접 연기해 채점하거나(매뉴얼), 가상 사용자(LLM)로 우리 시스템과 비교군(질문 무작위, LLM 자유 대화)을 자동 평가합니다([docs/evaluation.md](docs/evaluation.md)).

### 대화 예시 (실제 실행 결과, 2026-10-31)

평가 run 7의 한 대화다. 사용자는 실제 Amazon 구매 리뷰로 만든 가상 사용자(영어)이고, 챗봇은 실제로 산 상품을 1위로 추천했다.

```
User: Looking for a wireless headset for my laptop for work.             → 연결 방식: 무선, 마이크: 필요
Bot:  Nice—wireless with a mic for laptop work, got it. Do you want something that fully wraps your ears,
      small buds that tuck into your ear, pads that sit on your ears, or something that leaves your ears open?
User: Pads that sit on my ears — lightweight on-ear style so they don't pinch during long shifts.   → 착용 방식: 온이어
Bot:  Nice — lightweight on-ear pads that don't pinch during long shifts. Roughly what price range are you
      thinking: under $20, $20–$60, or $60+?
User: I'm aiming for the $20–$60 range — must have a solid working mic.  → 가격대: $20~60
Bot:  Do you work somewhere noisy, or do you need to hear people/alerts around you?
User: I work from home in a pretty quiet space ... don't need heavy noise isolation.               → ANC: 불필요
Bot:  ... do you ever wear them outside or during sweaty walks where they might get wet?
User: Mostly at home — I won't be sweating in them.                      → 방수: 불필요
Bot:  Got it, I'll keep Water/sweat resistance: Not needed in mind. Here's what I picked from what you told me. ...
```
(화살표는 Developer Panel의 메모리에 남은 값. 질문 아래에는 보기 버튼도 함께 나온다. 일부 문장은 줄였다.)
이 대화는 주 용도 속성이 남아 있던 때의 것이다. 사용자가 말한 "casual gaming"이 정답 데이터의 용도(`gaming`)와 우연히 맞았다. 주 용도는 이후(2026-11-03) 속성에서 뺐다([docs/evaluation.md](docs/evaluation.md#5-결과)).

## 로컬에서 실행하기

### 준비물

| 도구 | 버전 | 용도 |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 0.13 이상 | Python 3.12 설치와 백엔드 의존성 관리 (Python을 따로 설치할 필요 없음) |
| Node.js | 20 이상 (확인: 26) | 프론트엔드 빌드·개발 서버 |
| OpenAI API 키 | 선택 | 자유 입력 해석, 질문 작성, 상품·질의 임베딩, 평가. 없으면 보기 버튼과 보기 이름 매칭, YAML 고정 질문, 키워드 정렬 없이 평점순으로 대화한다 |

### 1. 설치

```bash
git clone <저장소 주소> RecommandShopping && cd RecommandShopping

cd backend
uv sync                     # .venv 생성, 의존성 설치
cp .env.example .env        # OPENAI_API_KEY=sk-... 입력 (선택)
cd ../frontend
npm install
```

### 2. 데이터 준비

가장 빠른 방법은 저장소에 포함된 카탈로그를 DB에 넣는 것이다. 원본에서 직접 만드는 방법은 [데이터 준비](#데이터-준비)에 있다.

```bash
cd backend
uv run python scripts/load_catalog.py headphones   # → loaded 4,628 products -> data/app.db
uv run python scripts/build_embeddings.py headphones   # 상품 임베딩 (OpenAI 키 필요, 약 $0.04, 선택)
```

임베딩은 추천할 때 조건이 똑같이 맞는 상품을 대화와 가까운 순으로 정렬하는 데 쓴다. 없으면 키워드 검색(BM25)만 쓴다.

### 3. 실행 (개발 모드)

터미널 두 개를 쓴다. 코드를 고치면 양쪽 모두 자동으로 다시 로드된다.

```bash
# 터미널 1: API 서버 (http://127.0.0.1:8000, API 문서 /docs)
cd backend && uv run uvicorn app.main:app --reload

# 터미널 2: 프론트 개발 서버 (/api 요청은 8000으로 프록시)
cd frontend && npm run dev
```

브라우저에서 **http://localhost:5173** 을 연다. Vite가 IPv6 `localhost`에만 listen하므로 `127.0.0.1:5173`으로는 열리지 않는다.
한 포트로 띄우는 방법은 [배포](#배포)에 있다.

## 데이터 준비

카탈로그(헤드폰·이어폰 4,628개와 추출된 속성)를 DB에 넣는 방법은 두 가지다.

| 방법 | 명령 | 시간 | 디스크 | 언제 |
|---|---|---|---|---|
| A. 저장소의 카탈로그 사용 | `scripts/load_catalog.py headphones` | 1초 미만 | 16MB (JSON) | 앱 실행, 데모, 평가 |
| B. 원본에서 다시 만들기 | `scripts/prepare_data.sh headphones` | 다운로드 + 약 10초 | 약 5.1GB | 속성 규칙·샘플링을 바꿨거나 새 카테고리를 추가할 때 |

### 방법 B: 원본에서 만들기

```bash
cd backend
scripts/prepare_data.sh headphones
```

[prepare_data.sh](backend/scripts/prepare_data.sh)는 아래 세 단계를 차례로 실행한다. 다시 실행해도 안전하다.
- 이미 받은 원본은 HuggingFace의 파일 크기와 비교해 같으면 건너뛴다.
- 받다가 끊기면 이어받는다.
- 같은 입력이면 같은 카탈로그가 나온다(샘플링 seed 고정).

| 단계 | 스크립트 | 입력 → 출력 | 내용 |
|---|---|---|---|
| 1. 다운로드 | `curl` | HuggingFace → `data/raw/meta_Electronics.jsonl` (5.25GB, 161만 상품) | [Amazon Reviews 2023](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023) Electronics 메타데이터 |
| 2. 필터링 | [filter_category.py](backend/scripts/filter_category.py) | raw → `data/interim/headphones.jsonl` (188MB, 56,048개) | 카테고리 경로가 `Headphones & Earbuds`인 상품만 |
| 3. 빌드 | [build_catalog.py](backend/scripts/build_catalog.py) | interim → `data/app.db`, `data/catalog/headphones.json` | 아래 다섯 단계를 거친다 |

빌드 단계의 세부 처리:
1. 가격이 있고 리뷰 100개 이상인 후보만 남긴다(5,099개).
2. 제목이 같은 상품을 하나로 줄인다(4,909개).
3. 규칙으로 속성 8개를 추출한다.
4. 연결 방식·착용 방식이 확인된 상품을 모두 남긴다(4,628개). YAML에 `sampling.size`를 정하면 그 개수만큼 비율을 유지해 층화 추출한다.
5. SQLite와 JSON으로 저장한다.

어떤 원본 파일·카테고리를 쓰고 어떤 규칙으로 속성을 뽑을지는 [backend/categories/headphones.yaml](backend/categories/headphones.yaml)에 정의되어 있다.

단계별로 직접 실행하거나 확인할 때는 아래 명령을 쓴다.

```bash
uv run python scripts/filter_category.py headphones
uv run python scripts/build_catalog.py headphones --dry-run    # 저장하지 않고 속성별 분포(후보 vs 카탈로그)만 출력
uv run python scripts/build_catalog.py headphones              # 저장
uv run python scripts/build_catalog.py headphones --no-sample  # 샘플링 설정을 무시하고 중복 제거 후 전체(4,909개, 연결 방식·착용 방식 unknown 포함) 저장
uv run python scripts/analyze_headphones.py                    # 데이터 분석 리포트 (docs/data-analysis.md의 근거)
```

**결과를 git에 반영할 때**
- `data/catalog/<id>.json`을 커밋한다. 다른 사람은 방법 A로 같은 카탈로그를 쓸 수 있다.
- `data/raw`, `data/interim`, `*.db`는 git에서 제외되어 있다.

**수동 검증 목록**
- `uv run python scripts/export_validation.py headphones --n 40`으로 `docs/validation/headphones_validation.csv`를 만든다.
- 이미 작성 중인 검증 결과를 **덮어쓰므로** 주의한다.

**새 카테고리 추가**
- `categories/<id>.yaml`을 작성하고 `scripts/prepare_data.sh <id>`를 실행한다. 코드는 고치지 않는다.
- 자세한 절차: [docs/category-spec.md](docs/category-spec.md)
- 서비스할 카테고리는 환경 변수 `DEFAULT_CATEGORY`로 정한다.

### 평가 데이터: Amazon-C4 (선택)

[Amazon-C4](https://huggingface.co/datasets/McAuley-Lab/Amazon-C4)는 Amazon Reviews 2023의 5점 리뷰를 ChatGPT가 1인칭 요구 문장으로 바꿔 쓴 질의와, 그 리뷰의 상품(정답)의 쌍이다(질의 21,223개).
LLM이 바꿔 쓴 반합성 데이터라 실제 구매 전 문장은 아니지만, 자연어 첫 발화로 추천까지 평가하는 데 쓸 수 있다.

```bash
cd backend
uv run python scripts/download_amazon_c4.py                   # 질의(test.csv, 12.5MB) + README → data/raw/amazon-c4/
uv run python scripts/download_amazon_c4.py --with-item-pool  # BLaIR 평가용 상품 풀(643MB)까지
```

- [download_amazon_c4.py](backend/scripts/download_amazon_c4.py)는 데이터셋 버전(커밋)을 고정해 받는다. 크기와 해시를 검증하고, 받은 내역을 `MANIFEST.json`에 남긴다.
- 이미 받은 파일은 건너뛰고, 끊기면 이어받는다.
- 정답 상품이 헤드폰 카탈로그(4,628개)에 있는 질의는 126개(상품 105개)다. 헤드폰 카테고리 전체로 넓히면 163개다.

## 배포

| 모드 | 프로세스 | 주소 | 용도 |
|---|---|---|---|
| 개발 | uvicorn(8000) + Vite 개발 서버(5173) | http://localhost:5173 | 코드 수정, 자동 리로드 |
| 단일 서버 | uvicorn 하나 | http://<호스트>:8000 | 데모, 공유, 서버 배포 |

단일 서버 모드는 아래처럼 동작한다.
- 프론트를 빌드하면 FastAPI가 `frontend/dist`를 찾아 `/`에서 화면을 서비스한다. `/api`는 API로 간다([`create_app`](backend/app/main.py "sym:py:app.main#create_app")).
- 같은 출처라 CORS 설정이나 프록시가 필요 없다.

### 단일 서버로 띄우기

```bash
# 1. 프론트 빌드 → frontend/dist
cd frontend && npm ci && npm run build

# 2. 백엔드 (데이터 준비가 끝난 상태에서)
cd ../backend
uv sync --no-dev
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

http://<호스트>:8000 으로 접속한다.
- 다른 기기에서 접속하려면 `--host 0.0.0.0`이 필요하다.
- 프론트를 고쳤다면 `npm run build`를 다시 하고 서버를 재시작한다.

### 환경 변수

`backend/.env` 또는 셸 환경 변수로 설정한다. 이름은 대소문자를 가리지 않는다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | (없음) | 없으면 LLM을 끄고 보기 버튼·보기 이름 매칭, 고정 질문만 쓴다 |
| `OPENAI_MODEL` | `gpt-5-mini` | 파서 모델(평가의 가상 사용자·심판도). 선택 근거: [docs/llm-parser.md](docs/llm-parser.md#3-평가) |
| `OPENAI_REASONING_EFFORT` | `low` | 추론 강도. 추론을 지원하지 않는 모델이면 비운다 |
| `OPENAI_QUESTION_MODEL` | (비우면 `OPENAI_MODEL`) | 질문 작성기 모델 |
| `OPENAI_QUESTION_REASONING_EFFORT` | `low` | 질문 작성기 추론 강도 |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | 동률 정렬의 임베딩 모델. 바꾸면 `build_embeddings.py`를 다시 실행 |
| `OPENAI_EMBEDDING_DIMENSIONS` | `512` | 임베딩 차원 |
| `OPENAI_TIMEOUT` | `30` | LLM 호출 제한 시간(초). 넘기면 보기 이름 매칭으로 대신한다 |
| `DB_PATH` | `backend/data/app.db` | SQLite 파일 (세션·대화·카탈로그·상품 임베딩·평가 기록) |
| `DEFAULT_CATEGORY` | `headphones` | 새 대화가 다루는 카테고리. 해당 카탈로그가 DB에 있어야 한다 |
| `FRONTEND_DIST` | `frontend/dist` | 빌드된 프론트 위치. `index.html`이 없으면 API만 서비스한다 |

### 운영 시 참고
- **저장소:** SQLite 파일 하나에 대화 기록과 카탈로그가 함께 있다. 백업이나 이전은 `DB_PATH` 파일을 복사하면 된다. 서버를 옮기면 데이터 준비(방법 A)를 다시 하거나 DB 파일을 함께 옮긴다.
- **동시성:** 과제·데모 규모를 기준으로 했다. 요청마다 연결을 열고 닫으며, 여러 사용자가 동시에 쓰는 운영 부하는 검증하지 않았다.
- **HTTPS·도메인:** uvicorn 앞에 리버스 프록시(Caddy, nginx 등)를 두고 8000으로 넘기면 된다. 프록시 설정 파일은 저장소에 없다.
- **API 키:** `.env`는 git에서 제외되어 있다. 키는 서버 환경 변수나 비밀 관리 도구로 주입한다.
- **비용:** 자유 입력 한 번에 해석 2회(요청 분류·속성 추출, 동시)와 질문 작성 1회를 호출한다. 해석만 1천 턴에 약 $1.7다(gpt-5-mini). 보기 버튼 답은 질문 작성만 호출한다. 추천하는 턴에는 질의 임베딩 1회가 더 든다(상품 임베딩은 미리 만들어 둠, 처음 한 번 약 $0.04). 평가 1회(시나리오 10개 × 3개 시스템)는 가상 사용자·심판 호출이 더해져 몇십 센트 수준이다(정확히 집계하지 않음).
- **Docker:** 이미지는 제공하지 않는다(개발 환경에 Docker가 없어 검증하지 못함).

## 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| 화면에 "'headphones' 카탈로그가 없습니다" (503) | DB에 카탈로그가 없다. `uv run python scripts/load_catalog.py headphones` |
| `127.0.0.1:5173`이 안 열림 | 개발 서버는 `localhost:5173`으로 연다 |
| 자유 입력에 "음, 잘 못 알아들었어. 아래 보기에서 골라 줄래?"만 나옴 | `OPENAI_API_KEY`가 없거나 호출에 실패했다(서버 로그 확인). 보기 버튼은 키 없이 동작한다 |
| 추천 안내에 "평점이 좋은 순으로 골랐어"만 나오고 대화와 가까운 순 정렬이 안 됨 | 상품 임베딩이 없거나 키가 없다. `uv run python scripts/build_embeddings.py headphones` |
| `http://localhost:8000/`이 404 | 프론트를 빌드하지 않았다. `cd frontend && npm run build` 후 서버 재시작 |
| `prepare_data.sh`에서 다운로드가 끊김 | 다시 실행하면 이어받는다 |
| 문서 링크 검사 실패(pytest) | 심볼을 옮기거나 이름을 바꿨다. `uv run --project backend python tools/doclinks/doclinks.py sync`로 경로를 고치고, 이름 변경은 문서의 `sym:`을 직접 수정 |

## 구조

```mermaid
flowchart LR
    UI[React 채팅 UI<br/>+ Developer Panel] -- /api --> API[FastAPI]
    API --> CHAT[대화 관리자]
    CHAT --> PARSER[LLM 파서<br/>요청 분류 ∥ 속성 추출] --> OPENAI[OpenAI API]
    CHAT --> WRITER[질문 작성기<br/>친구 페르소나] --> OPENAI
    CHAT --> ENGINE[IG 엔진<br/>순수 Python]
    CHAT -. 추천 때 .-> REL[동률 정렬<br/>BM25 + 임베딩] --> OPENAI
    CHAT --> CATALOG[카탈로그]
    CATALOG --> DB[(SQLite)]
    YAML[categories/*.yaml] -.-> CATALOG
```

```
backend/
  app/
    api/          세션·대화 API, 평가 API
    services/     대화 관리자(chat.py), 대화 상태(state.py), LLM 파서(parser.py: 요청 분류, extractor.py: 속성 추출),
                  대화 메모리(memory.py), 질문 작성기(questioner.py), 응답 문구(responses.py, 한국어·영어), 텍스트 관련도(relevance.py)
    core/         IG 엔진: belief tracker, question selector, recommender (웹·DB·LLM과 분리)
    catalog/      카테고리 정의 스키마, 속성 추출, 샘플링, DB 저장, BM25 검색
    llm/          LLM 클라이언트 (provider 인터페이스 + OpenAI)
    evaluation/   평가: C4 시나리오, 가상 사용자, 비교군, LLM 심판, 실행기
  categories/     카테고리 정의 YAML (속성·값·대체 질문 문구·추출 규칙)
  scripts/        데이터 파이프라인(prepare_data.sh 등), 상품 임베딩, 엔진 데모, 파서·대화 평가
  evaluation/     파서 평가 세트, 평가 시나리오(scenarios/<id>.jsonl)
  data/           raw·interim(git 제외), catalog/<id>.json(git 포함)
  tests/
frontend/src/     Vite + React + TypeScript
tools/doclinks/   문서 ↔ 코드 링크 검사·갱신 도구
docs/             설계·분석·관리 문서
```

자세한 구조와 한 턴의 처리 흐름은 [docs/architecture.md](docs/architecture.md)에 있습니다.

## 테스트와 도구

```bash
cd backend
uv run pytest                                   # 단위·API 테스트 + 저장소 문서 링크 검사 (실제 LLM 호출 없음)
uv run python scripts/ig_demo.py headphones --interactive          # 엔진만으로 대화 (LLM 없음)
uv run python scripts/ig_demo.py headphones --target B0BHR5VQC9 --policy random
uv run python scripts/eval_parser.py --verbose  # LLM 파서 평가 (실제 API, 63개 케이스)
uv run python scripts/run_eval.py headphones --limit 10   # 대화 평가: IG / Random / LLM (실제 API)

cd ..
uv run --project backend python tools/doclinks/doclinks.py sync    # 심볼을 옮긴 뒤 문서 링크 경로 갱신
```

## 평가 지표

대화 평가([docs/evaluation.md](docs/evaluation.md))에서 쓰는 지표의 정의와 계산식이다. 계산은 [runner.py](backend/app/evaluation/runner.py)의 `summarize`, `attribute_match`와 [systems.py](backend/app/evaluation/systems.py)의 `target_position`, `rank_percent`에 있다.

**기호**
- 시스템 s의 에피소드(가상 사용자와의 대화 한 번) 집합을 E, 오류 없이 끝난 에피소드를 E′ ⊆ E라 한다. 아래 평균·중앙값은 모두 E′에서, 값이 없는(None) 에피소드는 빼고 계산한다.
- 에피소드 e의 정답 상품(실제로 산 상품)을 t, 대화가 끝난 시점의 후보 목록(카탈로그 4,628개 중 사용자가 뺀 상품을 제외)을 C, 그 크기를 N = |C|라 한다.

### 1. 정답 순위 백분율 (`rank_pct`) — 주 지표

마지막 상태에서 C를 사용자에게 보여 주는 추천 순서대로 정렬했을 때 t의 위치(1부터)를 r(e)라 한다. 정렬 기준은 다음 순서다.
1. 말한 조건을 어기지 않는 상품이 먼저, 어기는 상품이 뒤
2. 그 안에서 확률(belief) 내림차순
3. 확률이 같으면 동률 정렬: 가격 요청 → 텍스트 관련도 → 평점

$$
\text{rank\_pct}(e) = 100 \times \frac{r(e)}{N}
$$

"상위 몇 %"라는 뜻이고 **작을수록 좋다**. 1위면 100/4628 ≈ 0.02%, 꼴찌면 100%다. 소수 셋째 자리에서 반올림한다. 엔진 순위가 없는 `llm` 비교군은 계산하지 않는다(값 없음).

$$
\text{rank\_pct\_mean}(s) = \frac{1}{|E'|}\sum_{e \in E'} \text{rank\_pct}(e), \qquad
\text{rank\_pct\_median}(s) = \operatorname{median}_{e \in E'} \text{rank\_pct}(e)
$$

- 평균은 나쁜 대화(정답이 크게 밀린 경우)까지 반영하고, 중앙값은 대표적인 대화를 본다. 둘을 함께 본다.
- 중앙값은 개수가 짝수면 가운데 두 값의 평균이다.
- **`rank_pct_*_no_text`:** 같은 대화의 마지막 상태에서 동률 정렬의 텍스트 관련도만 빼고(가격 요청 → 평점) 다시 계산한 값이다. 텍스트 관련도 정렬의 효과를 같은 대화끼리 비교하려고 남긴다.
- **Hit@k·MRR·절대 순위를 쓰지 않는 이유:** 4,628개 중 정답 하나를 정확히 맞히는 일은 거의 없어 Hit@k는 늘 0에 가깝고, MRR은 그 드문 적중에 좌우된다. 절대 순위("278위")는 카탈로그 크기를 모르면 뜻이 없다.

### 2. 1위 상품 속성 일치 (`attribute_match`)

정답 상품의 알려진 속성(지금 카테고리 정의에 있는 것만)을 T = {(a, v)}, 추천 1위 상품의 속성 값을 top₁(a)라 할 때

$$
\text{attribute\_match}(e) = \frac{\lvert \{(a, v) \in T : \text{top}_1(a) = v\} \rvert}{\lvert T \rvert}
$$

추천이 없거나 T가 비면 값이 없다. 시스템 값은 E′에서의 평균이다. 정답 그 상품이 아니어도 "같은 종류의 상품을 1위로 골랐는가"를 본다(0~1, 클수록 좋다). 세 시스템 모두 계산한다.

### 3. 질문 수 (`questions`), 확인 질문 수 (`confirms`)

- `questions(e)`: 추천하기 전까지 시스템이 한 응답 중 추천 전 확인 질문을 뺀 수(IG 질문, 다시 묻기, 안내 포함)
- `confirms(e)`: 추천 전 확인 질문("정리해 볼게: …. 이대로 찾아볼까?")의 수, 0~3
- 시스템 값은 각각 E′에서의 평균이다. 적을수록 대화가 짧다(같은 정확도라면 좋다).
- 대화는 시스템 응답 11번 안에 추천하지 않으면 추천 없이 끝난다.

### 4. LLM 심판 점수 (`fit`, `naturalness`, `efficiency`)

LLM 심판([judge.py](backend/app/evaluation/judge.py))이 가상 사용자의 숨겨진 선호, 대화 전체, 추천 상품(상품명·가격·속성)을 보고 매기는 1~5 정수다(범위 밖이면 1 또는 5로 자른다). 시스템 값은 E′에서의 평균이고, 클수록 좋다.

| 지표 | 보는 것 |
|---|---|
| `fit` | 추천(특히 1위)이 숨겨진 선호를 얼마나 충족하는가. 상품 정보에 없는 것은 감점하지 않는다 |
| `naturalness` | 말투와 흐름이 자연스러운가. 딱딱한 설문 같으면 낮다 |
| `efficiency` | 같은 것을 다시 묻거나 쓸모없는 질문이 없는가. 질문이 적어도 추천이 맞으면 높다 |

### 5. 그 밖의 값

| 값 | 정의 |
|---|---|
| `episodes` | 오류 없이 끝난 에피소드 수 \|E′\| |
| `errors` | 오류로 끝난 에피소드 수 \|E \ E′\| |
| `recommended` | 추천까지 간 에피소드 수 |
| `seconds` | 에피소드 하나의 소요 시간(대화 + 심판, 초)의 평균 |
| `target_in_candidates` (`llm`만) | 검색 후보(BM25 상위 30개) 안에 정답이 있었는가 |

**해석할 때 주의:** 시나리오가 10개라 같은 설정도 실행마다 값이 크게 흔들린다(같은 시나리오의 순위 %가 실행마다 10.9 ↔ 21.2). 시스템 간 차이는 여러 번 실행한 결과를 합쳐서 본다.

## 평가 실행 기록 (run별 코드 변경)

평가 기록(run 1~13)은 저장소에 올린 앱 DB [backend/data/app.db](backend/data/app.db)의 `eval_runs`·`eval_episodes`에 있다. 받은 뒤 서버를 띄우면 **Developer Panel → 평가 모드 → 자동 평가**에서 run별 요약표와 대화 기록·심판 코멘트를 볼 수 있다. 같은 DB에 카탈로그와 상품 임베딩도 들어 있다.

**의미 있는 run은 run 3부터다.** run 1·2·4·6은 시험 실행이라 비교에 쓰지 않는다(1: 시나리오 1개, 2: 조건 없이 뽑은 이전 시나리오, 4: run 3의 앞 5개 재실행, 6: 대화 2개). **지금 구조와 바로 비교할 수 있는 것은 run 8부터다.** run 7까지는 주 용도 속성이 실제로 쓰였고(run 7은 정정 참고), run 3·5는 한국어 시나리오였다.

각 run은 커밋 직전의 작업 코드로 돌았고, 그 코드는 표의 "코드" 커밋에 들어 있다. 시각은 한국 시간(DB의 `created_at`은 UTC).

순위 %는 정답 순위 백분율의 평균 / 중앙값이다(작을수록 좋다, [평가 지표](#평가-지표)). 시나리오는 Amazon-C4 질의 10개로 모든 run이 같다. run 3·5는 한국어 시나리오, run 7부터는 같은 질의로 다시 만든 영어 시나리오다.

| run | 시각 | 이 run에 새로 들어간 변경 | 코드 | ig 순위 % | random 순위 % | ig 속성 일치 / 질문 수 | ig 심판 적합 / 효율 |
|---|---|---|---|---|---|---|---|
| 3 | 10-31 04:32 | 기준: 대화 메모리, 속성 추출 별도 호출, 친구 페르소나 질문(돌려 묻기), 추출 결과 그대로 믿기, C4 시나리오(리뷰 300자 이상) | `29ae8aa` | 8.6 / 5.8 | 3.1 / 2.2 | 0.83 / 5.2 | 2.0 / 2.4 |
| 5 | 10-31 09:26 | 예산은 직접 묻기(`ask_directly`), 장소만으로 기능 짐작 금지, 장면 속 바람을 묻기 | `ec83d7d` | 6.3 / 6.5 | 4.5 / 4.0 | 0.86 / 4.6 | 2.7 / 3.1 |
| 7 | 10-31 10:27 | 영어 가상 사용자·심판, 응답 언어 따라가기, 동률 정렬(BM25 + 임베딩 RRF). ⚠ 주 용도 삭제는 DB 정의에 반영되지 않은 채 돌았다 | `ec83d7d` | 11.2 / 6.0 | 10.8 / 6.7 | 0.85 / 3.9 | 2.7 / 4.1 |
| 8 | 11-03 17:49 | 주 용도(use_case) 속성 삭제(실제 반영), 정의 스냅샷 불일치 경고. 지표를 순위 백분율로 교체 | `1fbf431` | 12.0 / 3.4 | 6.2 / 2.6 | 0.88 / 3.7 | 2.6 / 4.2 |
| 9 | 11-03 20:15 | 가격을 숫자 범위로("$15–25" → 실제 가격으로 판정) | `ba8f41b` | 3.95 / 1.3 | 4.8 / 2.2 | 0.93 / 3.7 | 2.9 / 4.3 |
| 10 | 11-03 20:56 | 짐작을 말로 드러내고 반박으로 바로잡기(`basis: guessed`), 가상 사용자가 틀린 짐작을 바로잡음 | `f5126f5` | 6.7 / 1.0 | 5.7 / 1.75 | 0.94 / 3.3 | 3.2 / 4.3 |
| 11 | 11-03 21:17 | 추천 전 확인 질문(최대 3번) | `f433117` | 3.7 / 1.3 | 2.2 / 1.4 | 0.92 / 3.5 (+확인 1.2) | 2.9 / 4.2 |
| 12 | 11-03 21:34 | 여러 값 조건(OR: "온이어나 오버이어", NOT: "인이어만 아니면") | `9e5a967` | **2.7** / 2.0 | 4.0 / 1.9 | **0.97** / 3.4 (+1.1) | 3.1 / **4.9** |
| 13 | 11-03 23:05 | 변경 없음 (run 12 반복) | `9e5a967` | 3.0 / **0.97** | 2.7 / 1.5 | **0.97** / 2.9 (+1.6) | 3.1 / 3.7 |
| 14 | 11-04 18:53 | 변경 없음. **새 시나리오 34개**(리뷰 300자 이상 후보 중 run 3~13의 10개를 뺀 나머지 전부, `--start 10 --limit 34`). 시나리오가 달라 위 run과 순위 %를 직접 비교하지 않는다 | `9e5a967` | 5.65 / 1.20 | 3.94 / 0.90 | 0.94 / 3.4 (+1.5) | 2.9 / 3.9 |

- **run별 핵심:** run 9(숫자 범위)에서 처음 ig가 random보다 평균·중앙값 모두 나아졌다. run 11(추천 전 확인)은 마지막 턴 짐작 사례(c4-5173 41% → 1.4%)를, run 12(OR·NOT)는 "오픈형도 괜찮아" 사례(c4-5123 20.9% → 10.9%)를 고쳤다. run 12 + 13을 합치면 ig 2.85 / 1.09, random 3.34 / 1.66이다. 다만 실행마다 ig와 random의 순서가 뒤집혀, 차이가 실행 간 흔들림보다 크다고는 아직 말할 수 없다.
- **비교할 때 주의**
  - run 3·5(한국어)와 run 7 이후(영어)는 가상 사용자의 언어·규칙이 달라 직접 비교는 참고용이다.
  - run 7은 YAML에서 주 용도를 뺐지만 DB의 정의 스냅샷을 다시 넣지 않아 실제로는 주 용도를 묻고 걸렀다([evaluation.md 5절](docs/evaluation.md#5-결과)의 정정).
  - run 3·5·7의 순위 %는 지표를 바꾼 뒤(2026-11-03) 저장된 순위로 다시 계산한 값이다. 1위 속성 일치는 당시 정의(주 용도 포함)로 계산된 값이다.
  - 대화 기록에 나오는 시나리오 페르소나는 그 run 당시 버전이다. run 3·5의 한국어 시나리오 파일은 지금 저장소에 없다(영어로 다시 만듦).
- **DB를 함께 쓸 때 주의:** `app.db`는 로컬에서 쓰면(대화, 평가 실행) 바로 바뀌는 바이너리 파일이라 git으로 합칠 수 없다. 각자 바뀐 DB는 커밋하지 말고, pull 전에 `git checkout -- backend/data/app.db`로 되돌린다(로컬에서 돌린 평가 기록은 사라진다). 새 평가 기록은 한 사람이 정해 올린다.

## 현재 결과

**대화 평가 run 12** (2026-11-03, [docs/evaluation.md](docs/evaluation.md#5-결과)): 실제 구매 기록(Amazon-C4) 시나리오 10개, 영어 가상 사용자
- 정답(실제로 산 상품)의 순위를 후보 수로 나눈 **순위 백분율**(상위 몇 %, 작을수록 좋음)로 봅니다. 평균 / 중앙값: **IG 2.7% / 2.0%**, 질문 무작위 4.0% / 1.9% (run 7: IG 11.2% / 6.0%).
- 주 용도 삭제, 가격 숫자 범위, 짐작을 말로 드러내기, 추천 전 확인 질문, 여러 값 조건(OR·NOT)을 차례로 넣었습니다. 1위 상품의 속성 일치 0.97, 질문 3.4개 + 확인 1.1개, LLM 심판 효율 4.9/5.
- 같은 설정을 한 번 더 돌린 run 13과 합치면(대화 20개) IG 2.85% / 1.09%, 질문 무작위 3.34% / 1.66%이고, 시나리오 10개 중 8개에서 IG가 나았습니다. 다만 실행마다 순서가 뒤집히고 시나리오가 10개뿐이라(부호 검정 p ≈ 0.055) 아직 확실하다고 말할 수는 없습니다.
- 정답 하나를 맞히는 Hit@k는 4,628개 중에서는 거의 늘 0이라 지표에서 뺐습니다.
- 시나리오 10개를 한 번씩 돌린 결과라 반복 확인이 필요합니다. 텍스트 관련도 정렬은 아직 효과가 없습니다. 전체 정리는 [docs/briefing.md](docs/briefing.md#6-결과-run-3--5--7).

**IG 엔진 예비 확인** ([docs/ig-engine.md](docs/ig-engine.md#6-예비-확인-결과-2026-10-26))
- 이전 150개 카탈로그에서, 상품 데이터대로 정확히 답하는 가상 사용자로 측정했습니다(LLM 없음).
- 질문 4회 후 Hit@3: **IG 0.63** / Fixed 0.46 / Random 0.32 (속성대로 정확히 답하는 시뮬레이션이라 Hit@3을 썼습니다. 대화 평가에는 쓰지 않습니다)

**LLM 파서** ([docs/llm-parser.md](docs/llm-parser.md#3-평가))
- 평가 세트 63개(한국어·영어, 돌려 물은 질문의 답 포함)에서 `gpt-5-mini` + reasoning `low`가 63/63을 통과했습니다.
- 비용은 1천 턴당 약 $1.7입니다(호출 2회).

## 한계와 진행 상황

- **데이터:** Amazon Reviews 2023(2023년 9월 시점) 기준이고 헤드폰·이어폰만 다룹니다. 가격이 있고 리뷰 100개 이상인 4,628개입니다.
- **속성 추출 오류:** Amazon 상품 정보 자체가 틀린 경우가 있습니다. 예: 오픈형 제품이 인이어로 표기됨([I-9](docs/issue.md#i-9)), 업무용 헤드셋의 용도가 게임으로 표기됨. 40개 수동 검증은 아직 하지 않았습니다.
- **대화 견고성:** "잘 몰라"나 질문과 무관한 말에는 한 번 더 쉽게 묻고 넘어갑니다. "노캔이 뭔데?", "일단 목록 볼래", "$30 미만" 같은 입력은 아직 처리하지 못합니다([roadmap Phase 3++](docs/roadmap.md#phase-3-대화-견고성-모름되묻기바로-보기숫자-가격)).
- **응답 시간:** 자유 입력 한 턴에 10~14초 걸립니다(해석 + 질문 작성, [I-12](docs/issue.md#i-12)).
- **평가:** 시나리오 10개, 한 번씩 실행, 가상 사용자와 심판이 같은 모델입니다([I-13](docs/issue.md#i-13)).

전체 계획은 [docs/roadmap.md](docs/roadmap.md), 진행 중인 작업은 [docs/ongoing.md](docs/ongoing.md), 미해결 이슈는 [docs/issue.md](docs/issue.md)에서 볼 수 있습니다.

## 문서

| 문서 | 내용 |
|---|---|
| [briefing.md](docs/briefing.md) | **팀 공유용 현재 상태 정리** (로직, 코드 지도, 평가, 최신 결과, 일 나누기) |
| [architecture.md](docs/architecture.md) | 시스템 구조, 대화 흐름, 데이터 파이프라인, DB 모델, 설계 원칙 |
| [ig-engine.md](docs/ig-engine.md) | 가능도 모델, 질문 선택, 종료 조건·한계 표시, 동률 정렬, 예비 결과 |
| [llm-parser.md](docs/llm-parser.md) | LLM 파서, 대화 메모리, 질문 작성기, 멀티턴 추천 조정, Safety·응답 언어, 모델 선택 |
| [evaluation.md](docs/evaluation.md) | 평가: C4 시나리오, 비교군, 지표, 평가 모드, 결과 |
| [category-spec.md](docs/category-spec.md) | 카테고리 정의 형식, 새 카테고리 추가 절차 |
| [data-analysis.md](docs/data-analysis.md) | 헤드폰 데이터 분석 (속성 선정 근거) |
| [doc-links.md](docs/doc-links.md) | 문서 ↔ 코드 링크 규칙과 도구 |
| [backend/README.md](backend/README.md) | 백엔드 실행, 환경 변수, API |
| [plan.pdf](docs/plan.pdf) | 원래 과제 계획 |
