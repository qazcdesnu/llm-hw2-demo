# 문서 ↔ 코드 링크

← [CLAUDE.md](../CLAUDE.md) · 관련: [architecture](architecture.md) · 도구: [doclinks.py](../tools/doclinks/doclinks.py)

문서에서 코드의 함수·클래스를 가리키되, 코드가 움직여도 링크가 깨지지 않게 하는 규칙과 도구.
**링크의 기준은 줄 번호가 아니라 심볼 ID**이고, 줄 번호는 도구가 코드 구조(Python `ast`, TypeScript 컴파일러 API)를 읽어 다시 쓴다.

## 1. 문서 → 코드 링크

표준 Markdown 링크의 title 자리에 심볼 ID를 넣는다.

```markdown
[`extract_attributes`](../backend/app/catalog/extract.py#L107 "sym:py:app.catalog.extract#extract_attributes")
[`DebugPanel`](../frontend/src/components/DebugPanel.tsx#L9 "docid:ui.debug-panel")
```

- **주소 (`…#L107`):** GitHub과 VS Code에서 클릭하면 그 줄로 간다. 직접 쓰지 않고 `sync`가 채운다. 처음엔 파일 경로만 적어도 된다.
- **title (`sym:…`, `docid:…`):** 링크의 기준이다. 사람이 쓰며, 마우스를 올리면 툴팁으로 보인다.

### 심볼 ID 형식

| 형식 | 예 | 의미 |
|---|---|---|
| `sym:py:<모듈>#<이름>` | `sym:py:app.catalog.spec#AttributeSpec._check_consistency` | Python. 모듈은 `backend/` 기준 점 경로, 이름은 `Class.method` 형태 |
| `sym:py:<모듈>` | `sym:py:app.core` | Python 모듈 전체 (주소에 줄 번호 없음) |
| `sym:ts:<경로>#<이름>` | `sym:ts:src/api#sendMessage` | TypeScript. 경로는 `frontend/` 기준, 확장자 제외 |
| `docid:<id>` | `docid:chat.handle-turn` | 코드에 직접 붙인 고정 ID (아래 2절) |

인덱싱 대상:
- Python: `backend/app`, `backend/scripts`의 모듈, 클래스(중첩 포함), 함수·메서드, 모듈·클래스 수준 변수
- TypeScript: `frontend/src`의 함수, 클래스와 멤버, interface, type, enum, 최상위 변수(`const` 컴포넌트 포함)

전체 목록은 `index` 명령으로 볼 수 있다 ([4절](#4-명령)).

### 코드가 바뀌었을 때

| 변화 | `sym:` 링크 | `docid:` 링크 |
|---|---|---|
| 같은 파일 안에서 줄 이동 | `sync`가 줄 번호 갱신 | 같음 |
| 다른 모듈로 이동 | 같은 언어에 같은 이름이 **하나뿐이면** `sync`가 title까지 갱신. 여럿이면 오류로 알림 | 그대로 따라감 |
| 이름 변경 | 오류 → 직접 수정 | 그대로 따라감 |
| 삭제 | 오류 | 오류 |

**언제 `docid`를 쓰나:** 여러 문서에서 참조하거나, 이름·위치가 바뀔 가능성이 큰 진입점에 쓴다.
예: [`handle_message`](../backend/app/services/chat.py#L323 "docid:chat.handle-turn")는 Phase 2~3에서 크게 바뀔 예정이라 `docid`를 붙였다.

## 2. 코드에 붙이는 태그

Python은 docstring에, TypeScript는 JSDoc(`/** */`) 주석에 같은 태그를 쓴다. 한 줄에 태그 하나다.

| 태그 | 의미 |
|---|---|
| `@docid <id>` | 이 심볼의 고정 ID. 저장소 전체에서 유일해야 함. 형식: `영역.이름` (예: `llm.client`, `ui.debug-panel`) |
| `@see <저장소 기준 경로>[#앵커]` | 이 코드를 설명하는 문서. 경로와 앵커가 실제로 있는지 검사함 |

```python
def handle_message(state: dict, message: str) -> TurnResult:
    """대화 한 턴을 처리한다.

    @docid chat.handle-turn
    @see docs/architecture.md#3-대화-한-턴의-흐름
    """
```

```ts
/**
 * Developer Panel: 엔진 내부 상태를 보여준다.
 *
 * @docid ui.debug-panel
 * @see docs/architecture.md#3-대화-한-턴의-흐름
 */
export default function DebugPanel(...) { ... }
```

**모듈(파일) 단위 태그**
- Python: 모듈 docstring에 쓴다.
- TypeScript: 파일 맨 위 주석에 쓴다. 단, 첫 선언과 **빈 줄로 떨어져 있어야** 파일 주석으로 인식한다. 붙어 있으면 첫 선언의 주석이 된다.

## 3. 문서 → 문서 링크

- 일반 상대 링크를 쓴다: `[표시 이름](경로.md)`, `[표시 이름](경로.md#앵커)`
- 앵커는 GitHub 규칙을 따른다: 소문자로 바꾸고, 공백은 `-`로, 그 밖의 문장부호는 제거.
- 이슈는 `[I-7](issue.md#i-7)`처럼 쓴다. 각 ID 칸에 `<a id>` 앵커가 있다.
- 코드 파일에 `#L10`처럼 줄 번호만 적은 링크는 심볼 ID가 없어 경고가 뜬다. 함수·클래스를 가리키면 심볼 링크를 쓴다.

## 4. 명령

저장소 루트에서 실행한다. TypeScript 인덱싱에는 `node`와 `frontend/node_modules`(`npm install`)가 필요하다.

```bash
uv run --project backend python tools/doclinks/doclinks.py check   # 검사만. 오류가 있으면 exit 1
uv run --project backend python tools/doclinks/doclinks.py sync    # 심볼 링크 주소 갱신 + 이동한 심볼 추적 + 검사
uv run --project backend python tools/doclinks/doclinks.py index   # 심볼 목록 (--json)
```

검사 항목:
- 문서 → 문서: 파일과 앵커가 존재하는지
- 문서 → 코드: 심볼이 존재하는지, 주소가 현재 위치와 같은지
- 코드 → 문서: `@see` 대상 파일·앵커가 존재하는지
- `@docid` 중복

`backend`의 `pytest`가 저장소 전체 링크 검사를 포함한다([`test_repository_docs_have_no_broken_links`](../backend/tests/test_doclinks.py)).
그래서 코드를 옮기고 `sync`를 잊으면 테스트가 실패한다.

## 5. 작업 규칙

- 코드를 고친 뒤에는 `sync`를 실행하고, 바뀐 문서도 함께 커밋한다.
- 문서에서 함수·클래스를 언급할 때는 심볼 링크를 쓴다. 파일 전체를 가리킬 때는 일반 경로 링크를 쓴다.
- 새 진입점이나 여러 문서가 참조하는 심볼에는 `@docid`를, 설계 문서가 있는 코드에는 `@see`를 단다.

## 6. 구현

| 부분 | 위치 |
|---|---|
| Python 인덱서 | [`index_python`](../tools/doclinks/doclinks.py) (표준 라이브러리 `ast`) |
| TypeScript 인덱서 | [ts_symbols.mjs](../tools/doclinks/ts_symbols.mjs) (`frontend/node_modules`의 `typescript`) |
| 링크 해석·이동 추적 | `Resolver` ([doclinks.py](../tools/doclinks/doclinks.py)) |
| 테스트 | [test_doclinks.py](../backend/tests/test_doclinks.py) |

`tools/`는 인덱싱 대상이 아니라서 위 표는 파일 링크로 적었다.
