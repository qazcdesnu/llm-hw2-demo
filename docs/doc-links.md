# 문서 ↔ 코드 양방향 링크

← [CLAUDE.md](../CLAUDE.md) · 관련: [architecture](architecture.md) · 도구: [doclinks.py](../tools/doclinks/doclinks.py)

문서와 코드를 양방향으로 연결하고, 두 방향이 어긋나지 않게 하는 규칙과 도구.

- **코드는 줄 번호가 아니라 심볼(객체·함수·클래스·모듈)로 연결한다.**
- **문서 → 코드 링크가 기준이고, 코드 → 문서 포인터(`@see`)는 그 역참조다.**
- 도구는 코드 구조를 파서(Python `ast`, TypeScript 컴파일러 API)로 읽는다. 그래서 검사와 갱신의 결과가 매번 같다(결정적).

## 1. 문서 → 코드 링크 (기준)

표준 Markdown 링크의 title 자리에 심볼 ID를 넣는다.

```markdown
[`extract_attributes`](../backend/app/catalog/extract.py "sym:py:app.catalog.extract#extract_attributes")
[`DebugPanel`](../frontend/src/components/DebugPanel.tsx "docid:ui.debug-panel")
[ig_demo.py](../backend/scripts/ig_demo.py)          ← 코드 파일 링크 = 그 모듈을 가리킴
```

- **주소:** 심볼이 있는 파일의 경로만 둔다. 줄 번호(`#L107`)는 넣지 않는다(넣으면 오류). 심볼이 다른 파일로 옮겨지면 `sync`가 경로를 고친다.
  - GitHub·VS Code는 함수 이름으로 바로 가는 링크를 지원하지 않아, 클릭하면 파일 맨 위가 열린다. 링크 글자와 툴팁에 객체 이름이 보인다.
- **title (`sym:…`, `docid:…`):** 링크가 가리키는 대상이다. 사람이 쓴다.

### 심볼 ID 형식

| 형식 | 예 | 의미 |
|---|---|---|
| `sym:py:<모듈>#<이름>` | `sym:py:app.catalog.spec#AttributeSpec._check_consistency` | Python. 모듈은 `backend/` 기준 점 경로, 이름은 `Class.method` 형태 |
| `sym:py:<모듈>` | `sym:py:app.core` | Python 모듈 전체 |
| `sym:ts:<경로>#<이름>` | `sym:ts:src/api#sendMessage` | TypeScript. 경로는 `frontend/` 기준, 확장자 제외 |
| `docid:<id>` | `docid:chat.handle-turn` | 코드에 직접 붙인 고정 ID (3절) |

인덱싱 대상(양방향 관리 대상):
- Python: `backend/app`, `backend/scripts`의 모듈, 클래스(중첩 포함), 함수·메서드, 모듈·클래스 수준 변수
- TypeScript: `frontend/src`의 함수, 클래스와 멤버, interface, type, enum, 최상위 변수(`const` 컴포넌트 포함)

`tests/`, `tools/`, YAML·셸 스크립트 같은 그 밖의 파일로 가는 링크는 단방향이다. 파일이 있는지만 검사한다.

### 코드가 바뀌었을 때

| 변화 | `sym:` 링크 | `docid:` 링크 |
|---|---|---|
| 같은 파일 안에서 줄 이동 | 영향 없음 | 영향 없음 |
| 다른 모듈로 이동 | 같은 언어에 같은 이름이 **하나뿐이면** `sync`가 title까지 갱신. 여럿이면 오류 | 그대로 따라감 |
| 이름 변경 | 오류 → 직접 수정 | 그대로 따라감 |
| 삭제 | 오류 | 오류 |

## 2. 코드 → 문서 포인터 (역참조, 생성됨)

문서의 어느 **절**에서 심볼을 링크하면, 그 심볼의 주석에 `@see <문서>#<절 앵커>`가 있어야 한다.
반대로 문서에 링크가 없는 `@see`는 있으면 안 된다. 두 집합이 정확히 같아야 하며, 빠지거나 남으면 오류다.

```
docs/architecture.md                          frontend/src/components/DebugPanel.tsx
## 1. 한눈에 보기                              /**
| 채팅 UI | [`DebugPanel`](… "docid:…") |  ⇄   *  Developer Panel: …
                                                 *  @docid ui.debug-panel
                                                 *  @see docs/architecture.md#1-한눈에-보기
                                                 */
```

- **생성:** `@see`는 사람이 쓰지 않는다. `sync`가 문서 링크에서 계산해 다시 쓴다.
- **절 앵커:** 링크 바로 앞 제목의 GitHub 앵커다. 제목 앞의 링크면 문서 경로만 쓴다. 한 심볼을 여러 절에서 링크하면 `@see`가 여러 줄이 되며, 정렬된 순서로 쓴다.
- **주석 위치:** 파서가 찾는다.

  | 대상 | 위치 |
  |---|---|
  | Python 모듈·클래스·함수 | docstring. 없으면 새로 만든다 |
  | Python 변수 | 바로 위 `# @see …` 주석 |
  | TypeScript | 선언 바로 앞 JSDoc(`/** */`). 모듈은 파일 맨 위, 첫 선언과 빈 줄로 떨어진 JSDoc |

- **바꾸는 범위:** `sync`는 `@see` 줄만 바꾼다. 설명 문장과 `@docid`는 그대로 둔다. `@see`를 넣었다 빼면 파일이 바이트 단위로 원래와 같다(테스트로 보장).
- **단방향 문서:** 작업 기록·계획 문서(`done.md`, `ongoing.md`, `issue.md`, `roadmap.md`)의 코드 링크는 역참조를 만들지 않는다. 작업 로그마다 코드에 `@see`가 쌓이지 않게 하기 위해서다. 목록은 [doclinks.py](../tools/doclinks/doclinks.py)의 `ONE_WAY_DOCS`에 있다.

## 3. `@docid`

이름·위치가 바뀌어도 링크가 유지되는 고정 ID. 사람이 붙인다(`sync`는 건드리지 않음).

```python
def handle_message(...):
    """대화 한 턴을 처리한다.

    @docid chat.handle-turn
    @see docs/architecture.md#1-한눈에-보기      ← sync가 생성
    """
```

- 저장소 전체에서 유일해야 한다. 형식은 `영역.이름`이다(예: `llm.client`, `ui.debug-panel`).
- 여러 문서에서 참조하거나 크게 바뀔 진입점에 붙인다.
  - 예: [`handle_message`](../backend/app/services/chat.py "docid:chat.handle-turn")

## 4. 문서 본문 검사

**링크**
- 일반 상대 링크를 쓴다: `[표시 이름](경로.md)`, `[표시 이름](경로.md#앵커)`. 파일과 앵커가 있는지 검사한다.
- 앵커는 GitHub 규칙을 따른다: 소문자로 바꾸고, 문장부호는 제거하고, 공백은 `-`로, 같은 제목이 반복되면 `-1`, `-2`를 붙인다. 제목 속 링크는 표시 글자만 쓴다.
- 이슈는 `[I-7](issue.md#i-7)`처럼 쓴다. 각 ID 칸에 `<a id>` 앵커가 있다.

**백틱 언급** (링크가 아니어도 검사)

| 언급 | 검사 |
|---|---|
| 코드·문서 경로 (`.py .ts .tsx .mjs .sh .yaml .md`) | 파일이 있는지. 폴더 없이 파일 이름만 쓰면 저장소 안에서 파일 이름으로 찾는다 |
| 코드 이름: `name()` 형태이거나 CamelCase로 시작하는 이름 | 인덱스에 있는지. `Class.method`, `method` 둘 다 인정한다 |

- 데이터 파일(`.json`, `.csv`)과 폴더는 로컬에만 있을 수 있어 검사하지 않는다.
- 우리 코드가 아닌 이름(라이브러리 등)은 [external_names.txt](../tools/doclinks/external_names.txt)에 추가한다.
- 함수 이름을 바꾸면, 문서 본문에 백틱으로 적어 둔 옛 이름도 오류로 잡힌다.

## 5. 명령

저장소 루트에서 실행한다. TypeScript 인덱싱에는 `node`와 `frontend/node_modules`(`npm install`)가 필요하다.

```bash
uv run --project backend python tools/doclinks/doclinks.py check   # 검사만. 오류가 있으면 exit 1
uv run --project backend python tools/doclinks/doclinks.py sync    # 문서 링크 경로 + 코드 @see 역참조 갱신, 이후 재검사
uv run --project backend python tools/doclinks/doclinks.py refs backend/app/core/engine.py   # 이 코드를 참조하는 문서 위치
uv run --project backend python tools/doclinks/doclinks.py index   # 심볼 목록 (--json)
```

검사 항목 (하나라도 오류면 실패):
1. 문서 → 문서: 파일·앵커가 있는지
2. 문서 → 코드: 심볼이 있는지, 주소가 현재 파일과 같은지, 줄 번호 링크 금지
3. 문서 백틱 언급: 경로·코드 이름이 실제로 있는지
4. **양방향 일치:** 코드의 `@see` 집합 = 문서 링크에서 계산한 역참조 집합
5. 코드 `@see` 대상 파일·앵커가 있는지, `@docid` 중복

## 6. 자동 검증 장치

| 장치 | 언제 | 하는 일 |
|---|---|---|
| **pytest** | `cd backend && uv run pytest` | 저장소 전체 검사(위 1~5)와 도구 자체 테스트. 깨지면 테스트 실패 |
| **Claude Code hook: 수정 직후** | Claude가 Edit·Write로 파일을 고칠 때마다 | 저장소를 검사해 오류를 Claude에게 알린다. 코드 파일을 고쳤으면 그 코드를 참조하는 문서 위치도 알려 설명을 함께 고치게 한다. 작업을 막지는 않는다 |
| **Claude Code hook: 종료 전** | Claude가 응답을 끝내려 할 때 | 오류가 있으면 끝내지 못하게 막고 `sync`와 수정을 요구한다. 한 번 막은 뒤에도 남아 있으면 사용자에게 알리고 통과시킨다(무한 반복 방지) |

- hook 설정: [.claude/settings.json](../.claude/settings.json)
- hook 진입점: [hook.py](../tools/doclinks/hook.py)
- Bash로 파일을 고친 경우에는 수정 직후 hook이 돌지 않는다. 이 경우는 종료 전 hook과 pytest가 잡는다.

## 7. 작업 규칙

- 문서에서 함수·클래스를 설명하면 심볼 링크를 건다. 그러면 코드 쪽 `@see`는 `sync`가 만든다.
- 코드를 옮기거나 문서 절을 바꾼 뒤에는 `sync`를 실행하고, 바뀐 코드·문서를 함께 커밋한다.
- `@see`를 손으로 고치지 않는다. 다음 `sync`가 문서 기준으로 되돌린다.
- 코드를 고칠 때 수정 직후 hook이 알려 주는 "참조하는 문서"의 설명이 아직 맞는지 확인한다. 링크가 유효한 것과 설명이 맞는 것은 다르다.

## 8. 구현

| 부분 | 위치 |
|---|---|
| Python 인덱서 (심볼, 주석 위치) | [doclinks.py](../tools/doclinks/doclinks.py)의 `index_python` (표준 라이브러리 `ast`) |
| TypeScript 인덱서 | [ts_symbols.mjs](../tools/doclinks/ts_symbols.mjs) (`frontend/node_modules`의 `typescript`) |
| 역참조 계산·검사 | [doclinks.py](../tools/doclinks/doclinks.py)의 `expected_backrefs`, `check_backrefs` |
| `@see` 다시 쓰기 | [backrefs.py](../tools/doclinks/backrefs.py) |
| hook | [hook.py](../tools/doclinks/hook.py) |
| 테스트 | [test_doclinks.py](../backend/tests/test_doclinks.py) (양방향, 왕복 결정성, 언급 검사 포함) |

`tools/`는 인덱싱 대상이 아니라서 위 표는 파일 링크로 적었다.
