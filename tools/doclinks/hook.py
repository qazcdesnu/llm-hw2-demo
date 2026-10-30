"""Claude Code hook: 문서 ↔ 코드 링크 상태를 Claude에게 알려 주고, 깨진 채로 끝내지 못하게 한다.

    post  PostToolUse(Edit|Write|MultiEdit). 바뀐 파일이 코드·문서면 저장소 전체를 검사해
          오류와 "방금 바꾼 코드를 참조하는 문서"를 additionalContext로 돌려준다. 막지 않는다.
    stop  Stop. 오류가 있으면 종료를 막고(decision: block) 고치게 한다.
          이미 한 번 막았는데(stop_hook_active) 남아 있으면 사용자에게만 알리고 통과시킨다.

stdin으로 hook 입력 JSON을 받고, 알릴 것이 있을 때만 stdout에 JSON을 쓴다. 항상 exit 0.
설정: .claude/settings.json

@see docs/doc-links.md
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import doclinks as d  # noqa: E402

WATCHED_SUFFIXES = {".md", ".py", ".ts", ".tsx", ".mjs"}
MAX_LINES = 20
SYNC_CMD = "uv run --project backend python tools/doclinks/doclinks.py sync"


def _errors() -> tuple[list[d.Symbol], list[d.Problem]]:
    symbols = d.build_index()
    problems, _ = d.collect_problems(symbols)
    return symbols, [p for p in problems if p.level == "error"]


def _lines(items: list[str]) -> str:
    shown = items[:MAX_LINES]
    more = f"\n… 외 {len(items) - MAX_LINES}개" if len(items) > MAX_LINES else ""
    return "\n".join(f"- {x}" for x in shown) + more


def post(payload: dict) -> dict | None:
    raw = (payload.get("tool_input") or {}).get("file_path") \
        or (payload.get("tool_response") or {}).get("filePath")
    if not raw:
        return None
    path = Path(raw).resolve()
    if path.suffix not in WATCHED_SUFFIXES or not path.is_relative_to(d.REPO):
        return None

    symbols, errors = _errors()
    parts = []
    if errors:
        parts.append(f"doclinks: 문서↔코드 링크 오류 {len(errors)}개. 경로·역참조(@see)는 `{SYNC_CMD}`로 "
                     f"고치고, 남는 것(이름 변경, 없는 대상)은 직접 고칠 것.\n" + _lines([str(p) for p in errors]))
    if path.suffix != ".md":
        # 양방향이 일치하면 @see는 문서 링크와 같은 정보라 문서 링크만 보여준다
        refs = [r for r in d.find_references([path], symbols) if not r[1].startswith("@see")]
        if refs:
            parts.append(f"doclinks: 방금 바꾼 {d._rel(path)}를 참조하는 문서. 설명이 코드와 아직 맞는지 확인할 것.\n"
                         + _lines(sorted({f"{where} → {target}" for where, target in refs})))
    if not parts:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n\n".join(parts)}}


def stop(payload: dict) -> dict | None:
    _, errors = _errors()
    if not errors:
        return None
    if payload.get("stop_hook_active"):
        return {"systemMessage": f"문서↔코드 링크 오류 {len(errors)}개가 남아 있습니다 (doclinks check)."}
    return {"decision": "block",
            "reason": f"문서↔코드 링크가 일관되지 않습니다. `{SYNC_CMD}`를 실행하고 남은 오류를 고친 뒤 끝내세요.\n"
                      + _lines([str(p) for p in errors])}


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    text = sys.stdin.read()
    payload = json.loads(text) if text.strip() else {}
    try:
        out = {"post": post, "stop": stop}[event](payload)
    except KeyError:
        out = {"systemMessage": f"doclinks hook: 알 수 없는 이벤트 {event!r}"}
    except Exception as e:  # hook 실패가 작업을 막지 않게 한다
        out = {"systemMessage": f"doclinks hook 실패: {e}"}
    if out:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
