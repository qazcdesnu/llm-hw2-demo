"""코드의 @see 역참조를 결정적으로 다시 쓴다 (doclinks sync에서 사용).

문서 → 코드 링크가 기준이고, 코드의 @see는 그 역참조다. 이 모듈은 심볼마다 계산된
@see 목록을 받아 docstring·JSDoc·주석의 @see 줄만 바꾼다. 설명 문장과 @docid는 그대로 둔다.

심볼의 주석 위치(doc_span)와 새로 넣을 위치(insert_line)는 인덱서가 파서로 구한다:
Python은 ast, TypeScript는 컴파일러 API.

@see docs/doc-links.md
"""

import re
from collections.abc import Iterable
from pathlib import Path

SEE_TEXT_RE = re.compile(r"^@see\s")
PY_COMMENT_SEE_RE = re.compile(r"^\s*#\s*@see\s")


class ManualFixNeeded(Exception):
    """자동으로 고칠 수 없는 형태 (접두사 붙은 docstring, 한 줄 정의 등)."""


def _is_see(text: str) -> bool:
    return bool(SEE_TEXT_RE.match(text.strip()))


def _strip_trailing_blank(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _separator(content: list[str]) -> list[str]:
    """설명과 @see 사이 빈 줄. 마지막 줄이 이미 태그(@docid 등)면 붙여 쓴다."""
    return [] if content and content[-1].strip().lstrip("*").strip().startswith("@") else [""]


# ---------------------------------------------------------------- Python docstring

def render_py_docstring(block: list[str], indent: int, see: list[str]) -> list[str]:
    """기존 docstring 줄들 → @see만 바꾼 줄들. 빈 docstring이 되면 []."""
    text = "\n".join(block)
    m = re.match(r'^(\s*)([rRuUbBfF]*)("""|\'\'\')', text)
    if not m or m.group(2) or "\\" in text:
        raise ManualFixNeeded("docstring에 접두사나 백슬래시가 있다")
    lead, quote = m.group(1), m.group(3)
    close = text.rfind(quote)
    if close < m.end():
        raise ManualFixNeeded("docstring 끝을 찾지 못했다")
    inner, after = text[m.end():close], text[close + 3:]
    content = _strip_trailing_blank([line for line in inner.split("\n") if not _is_see(line)])
    sub = " " * indent
    has_text = any(line.strip() for line in content)

    if not has_text:
        if not see:
            return []
        if len(see) == 1:
            return [f"{lead}{quote}@see {see[0]}{quote}{after}"]
        return ([f"{lead}{quote}@see {see[0]}"] + [f"{sub}@see {t}" for t in see[1:]]
                + [f"{sub}{quote}{after}"])

    if not see:
        if len(content) == 1:
            return [f"{lead}{quote}{content[0]}{quote}{after}"]
        return [f"{lead}{quote}{content[0]}", *content[1:], f"{sub}{quote}{after}"]

    return ([f"{lead}{quote}{content[0]}", *content[1:], *_separator(content)]
            + [f"{sub}@see {t}" for t in see] + [f"{sub}{quote}{after}"])


def new_py_docstring(indent: int, see: list[str]) -> list[str]:
    sub = " " * indent
    if len(see) == 1:
        return [f'{sub}"""@see {see[0]}"""']
    return [f'{sub}"""@see {see[0]}'] + [f"{sub}@see {t}" for t in see[1:]] + [f'{sub}"""']


# ---------------------------------------------------------------- Python 주석 (변수)

def render_py_comments(block: list[str], indent: int, see: list[str]) -> list[str]:
    kept = [line for line in block if not PY_COMMENT_SEE_RE.match(line)]
    return kept + [f"{' ' * indent}# @see {t}" for t in see]


# ---------------------------------------------------------------- JSDoc

def render_jsdoc(block: list[str], indent: int, see: list[str]) -> list[str]:
    text = "\n".join(block)
    start, end = text.find("/**"), text.rfind("*/")
    if start < 0 or end < start:
        raise ManualFixNeeded("JSDoc 범위를 찾지 못했다")
    suffix = text[end + 2:]
    inner = text[start + 3:end]
    if "\n" not in inner:
        content = [inner.strip()] if inner.strip() else []
    else:
        raw = inner.split("\n")
        first = raw[0].strip()
        content = ([first] if first else []) + [re.sub(r"^\s*\*\s?", "", line) for line in raw[1:]]
    content = _strip_trailing_blank([line for line in content if not _is_see(line)])
    if not any(line.strip() for line in content):
        content = []
    if not content and not see:
        return []
    ind = " " * indent
    out = [f"{ind}/**"] + [f"{ind} * {line}" if line.strip() else f"{ind} *" for line in content]
    if content and see and _separator(content):
        out.append(f"{ind} *")
    out += [f"{ind} * @see {t}" for t in see]
    out.append(f"{ind} */{suffix}")
    return out


def new_jsdoc(indent: int, see: list[str]) -> list[str]:
    ind = " " * indent
    return [f"{ind}/**"] + [f"{ind} * @see {t}" for t in see] + [f"{ind} */"]


# ---------------------------------------------------------------- 파일 단위 적용

def rewrite_file(path: Path, targets: Iterable[tuple[object, list[str]]]) -> list[str]:
    """(심볼, 기대 @see 목록)들을 파일에 반영한다. 고치지 못한 심볼의 사유 목록을 돌려준다.

    아래쪽 편집부터 적용해 위쪽 심볼의 줄 번호가 바뀌지 않게 한다.
    같은 줄에서 시작하면 더 깊은(나중에 선언된) 심볼을 먼저 고친다.
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    failures = []

    def anchor(item):
        sym = item[0]
        return (sym.doc_span[0] if sym.doc_span else sym.insert_line, sym.start)

    for sym, see in sorted(targets, key=anchor, reverse=True):
        try:
            if not sym.editable:
                raise ManualFixNeeded("한 줄 정의라 주석 위치가 없다")
            if sym.style == "py-comment":
                a = sym.doc_span[0] if sym.doc_span else sym.insert_line
                lines[a - 1:sym.insert_line - 1] = render_py_comments(
                    lines[a - 1:sym.insert_line - 1], sym.indent, see)
            elif sym.doc_span:
                a, b = sym.doc_span
                render = render_jsdoc if sym.style == "js-doc" else render_py_docstring
                new = render(lines[a - 1:b], sym.indent, see)
                if not new and sym.kind == "module" and a == 1 and b < len(lines) and not lines[b].strip():
                    b += 1  # 넣을 때 함께 넣은 빈 줄까지 지워 원래 파일로 되돌린다
                lines[a - 1:b] = new
            elif see:
                new = new_jsdoc(sym.indent, see) if sym.style == "js-doc" else new_py_docstring(sym.indent, see)
                if sym.kind == "module" and sym.insert_line == 1:
                    new = new + [""]  # 파일 머리 주석은 첫 선언과 빈 줄로 떨어뜨린다
                lines[sym.insert_line - 1:sym.insert_line - 1] = new
        except ManualFixNeeded as e:
            failures.append(f"{sym.ref}: {e}")

    path.write_text("\n".join(lines), encoding="utf-8")
    return failures
