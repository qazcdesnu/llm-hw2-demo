"""문서 ↔ 코드 링크 관리 도구.

문서에서 코드를 가리킬 때 줄 번호 대신 심볼 ID를 기준으로 삼는다.
링크 주소의 경로·줄 번호는 이 도구가 코드 구조(AST)를 보고 다시 쓰므로, 코드가 움직여도 링크가 유지된다.
형식과 규칙: docs/doc-links.md

    uv run --project backend python tools/doclinks/doclinks.py check   # 깨진 링크 검사 (실패 시 exit 1)
    uv run --project backend python tools/doclinks/doclinks.py sync    # 줄 번호·경로 갱신, 이동한 심볼 추적
    uv run --project backend python tools/doclinks/doclinks.py index   # 심볼 목록 출력

@see docs/doc-links.md
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]
PY_ROOT, PY_DIRS = REPO / "backend", ["app", "scripts"]
TS_ROOT, TS_DIRS = REPO / "frontend", ["src"]
TS_INDEXER = Path(__file__).with_name("ts_symbols.mjs")
SKIP_DIRS = {"node_modules", ".venv", ".pytest_cache", "__pycache__", "dist", ".git"}

# 코드 주석·docstring의 태그. JSDoc의 앞쪽 "*"도 허용한다.
TAG_RE = re.compile(r"^[\s*]*@(docid|see)\s+(\S+)", re.M)
# [text](href "title"). title이 sym:… 또는 docid:…이면 코드 심볼 링크.
LINK_RE = re.compile(r'\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)')
CODE_RE = re.compile(r"^```.*?^```[^\n]*$|`[^`\n]*`", re.M | re.S)
LINE_ANCHOR_RE = re.compile(r"^L\d+(-L\d+)?$")


@dataclass
class Symbol:
    ref: str  # sym:<lang>:<module>[#<qualname>]
    kind: str
    file: Path
    start: int
    end: int
    docid: str | None = None
    see: list[str] = field(default_factory=list)

    @property
    def qualname(self) -> str | None:
        return self.ref.split("#", 1)[1] if "#" in self.ref else None

    @property
    def lang(self) -> str:
        return self.ref.split(":")[1]


@dataclass
class Problem:
    level: str  # error | warning
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


def parse_tags(comment: str | None) -> tuple[str | None, list[str]]:
    docid, see = None, []
    for tag, value in TAG_RE.findall(comment or ""):
        if tag == "docid":
            docid = value
        else:
            see.append(value)
    return docid, see


# ---------------------------------------------------------------- 심볼 인덱스

def index_python(root: Path = PY_ROOT, dirs: list[str] = PY_DIRS) -> list[Symbol]:
    symbols = []
    for d in dirs:
        for path in sorted((root / d).rglob("*.py")):
            if SKIP_DIRS & set(path.parts):
                continue
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            module = ".".join(parts)
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            docid, see = parse_tags(ast.get_docstring(tree))
            symbols.append(Symbol(f"sym:py:{module}", "module", path, 1,
                                  max(1, source.count("\n")), docid, see))
            _walk_python(tree.body, module, "", path, symbols)
    return symbols


def _walk_python(body: list[ast.stmt], module: str, prefix: str, path: Path, out: list[Symbol]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qualname = prefix + node.name
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            docid, see = parse_tags(ast.get_docstring(node))
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            out.append(Symbol(f"sym:py:{module}#{qualname}", kind, path, start, node.end_lineno,
                              docid, see))
            # 함수 내부는 공개 심볼이 아니므로 클래스만 들어간다.
            if isinstance(node, ast.ClassDef):
                _walk_python(node.body, module, qualname + ".", path, out)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    out.append(Symbol(f"sym:py:{module}#{prefix}{t.id}", "variable", path,
                                      node.lineno, node.end_lineno))


def index_typescript(root: Path = TS_ROOT, dirs: list[str] = TS_DIRS) -> list[Symbol]:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node가 없어 TypeScript 심볼을 읽을 수 없다")
    result = subprocess.run([node, str(TS_INDEXER), str(root), *dirs],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"TypeScript 인덱서 실패 (frontend에서 npm install 필요?):\n{result.stderr}")
    symbols = []
    for raw in json.loads(result.stdout):
        docid, see = parse_tags(raw["comment"])
        symbols.append(Symbol(raw["ref"], raw["kind"], Path(raw["file"]), raw["start"], raw["end"],
                              docid, see))
    return symbols


def build_index() -> list[Symbol]:
    return index_python() + index_typescript()


# ---------------------------------------------------------------- Markdown

def markdown_files(root: Path = REPO) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if not SKIP_DIRS & set(p.relative_to(root).parts))


def heading_anchors(path: Path) -> set[str]:
    """GitHub 규칙의 제목 앵커와 <a id="…"> 앵커."""
    text = re.sub(r"^```.*?^```[^\n]*$", "", path.read_text(encoding="utf-8"), flags=re.M | re.S)
    anchors = set(re.findall(r'<a id="([^"]+)"', text))
    for title in re.findall(r"^#{1,6}\s+(.*?)\s*#*\s*$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", title.replace("`", "").strip().lower())
        anchors.add(slug.replace(" ", "-"))
    return anchors


def iter_links(text: str):
    """코드 블록·인라인 코드 밖의 링크 match."""
    code = [m.span() for m in CODE_RE.finditer(text)]
    for m in LINK_RE.finditer(text):
        if not any(s <= m.start() < e for s, e in code):
            yield m


def expected_href(md: Path, symbol: Symbol) -> str:
    rel = Path(os.path.relpath(symbol.file, md.parent)).as_posix()
    return rel if symbol.kind == "module" else f"{rel}#L{symbol.start}"


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


class Resolver:
    def __init__(self, symbols: list[Symbol]):
        self.by_ref = {s.ref: s for s in symbols}
        self.by_docid: dict[str, Symbol] = {}
        self.problems: list[Problem] = []
        for s in symbols:
            if s.docid is None:
                continue
            if s.docid in self.by_docid:
                other = self.by_docid[s.docid]
                self.problems.append(Problem("error", _rel(s.file),
                                             f"@docid {s.docid} 중복 ({s.ref}, {other.ref})"))
            self.by_docid[s.docid] = s
        self.by_qualname: dict[tuple[str, str], list[Symbol]] = {}
        for s in symbols:
            if s.qualname:
                self.by_qualname.setdefault((s.lang, s.qualname), []).append(s)

    def resolve(self, title: str) -> Symbol | None:
        if title.startswith("docid:"):
            return self.by_docid.get(title.removeprefix("docid:"))
        return self.by_ref.get(title)

    def relocate(self, title: str) -> Symbol | None:
        """모듈을 옮긴 심볼: 같은 언어·같은 이름의 심볼이 하나뿐이면 그것으로 본다."""
        if not title.startswith("sym:") or "#" not in title:
            return None
        lang, qualname = title.split(":")[1], title.split("#", 1)[1]
        candidates = self.by_qualname.get((lang, qualname), [])
        return candidates[0] if len(candidates) == 1 else None


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def check_path_link(md: Path, href: str) -> str | None:
    """일반 링크(파일·문서 앵커) 검사. 문제가 있으면 메시지."""
    path, _, anchor = href.partition("#")
    dest = (md.parent / unquote(path)).resolve() if path else md
    if not dest.exists():
        return f"없는 파일: {href}"
    if anchor and dest.suffix == ".md" and unquote(anchor) not in heading_anchors(dest):
        return f"없는 앵커: {href}"
    return None


def process_markdown(md: Path, resolver: Resolver, fix: bool) -> tuple[list[Problem], list[str]]:
    """문서 하나의 링크를 검사하고, fix면 심볼 링크의 주소를 고친다. (문제, 변경 내역)"""
    text = md.read_text(encoding="utf-8")
    problems, changes, edits = [], [], []

    for m in iter_links(text):
        label, href, title = m.group(1), m.group(2), m.group(3)
        where = f"{_rel(md)}:{line_of(text, m.start())}"
        if href.startswith(("http://", "https://", "mailto:")):
            continue

        if title and title.startswith(("sym:", "docid:")):
            symbol = resolver.resolve(title)
            new_title = title
            if symbol is None:
                symbol = resolver.relocate(title)
                if symbol is None:
                    problems.append(Problem("error", where, f"찾을 수 없는 심볼: {title}"))
                    continue
                new_title = symbol.ref
                if not fix:
                    problems.append(Problem("error", where, f"심볼이 이동함: {title} → {symbol.ref} (sync로 갱신)"))
                    continue
                changes.append(f"{where}: {title} → {symbol.ref}")
            want = expected_href(md, symbol)
            if href != want or new_title != title:
                if fix:
                    edits.append((m.span(), f'[{label}]({want} "{new_title}")'))
                    if href != want:
                        changes.append(f"{where}: {href} → {want}")
                else:
                    problems.append(Problem("error", where, f"오래된 위치: {href} (현재 {want}, sync로 갱신)"))
            continue

        message = check_path_link(md, href)
        if message:
            problems.append(Problem("error", where, message))
        elif "#" in href and LINE_ANCHOR_RE.match(href.split("#", 1)[1]):
            problems.append(Problem("warning", where,
                                    f"줄 번호 링크에 심볼 ID가 없어 코드가 바뀌면 어긋남: {href}"))

    if fix and edits:
        for (start, end), replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        md.write_text(text, encoding="utf-8")
    return problems, changes


def check_code_references(symbols: list[Symbol]) -> list[Problem]:
    """코드의 @see <repo 기준 경로>[#앵커] 검사."""
    problems = []
    for s in symbols:
        for target in s.see:
            message = check_path_link(REPO / "_", target)  # REPO 기준으로 해석
            if message:
                problems.append(Problem("error", f"{_rel(s.file)}:{s.start}", f"@see {message}"))
    return problems


def run(fix: bool) -> int:
    symbols = build_index()
    resolver = Resolver(symbols)
    problems = list(resolver.problems) + check_code_references(symbols)
    changes: list[str] = []
    for md in markdown_files():
        p, c = process_markdown(md, resolver, fix)
        problems += p
        changes += c
    for c in changes:
        print(f"UPDATED {c}")
    for p in problems:
        print(p)
    errors = sum(p.level == "error" for p in problems)
    print(f"{len(symbols)} symbols, {len(markdown_files())} docs, "
          f"{errors} errors, {len(problems) - errors} warnings, {len(changes)} updates")
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="링크 검사 (문서→코드, 문서→문서, 코드 @see→문서)")
    sub.add_parser("sync", help="심볼 링크의 경로·줄 번호 갱신, 이동한 심볼 추적")
    p_index = sub.add_parser("index", help="심볼 목록")
    p_index.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "index":
        symbols = build_index()
        if args.json:
            print(json.dumps([{"ref": s.ref, "kind": s.kind, "file": _rel(s.file), "start": s.start,
                               "end": s.end, "docid": s.docid, "see": s.see} for s in symbols],
                             ensure_ascii=False, indent=1))
        else:
            for s in symbols:
                docid = f"  docid:{s.docid}" if s.docid else ""
                print(f"{s.ref}  {_rel(s.file)}:{s.start}{docid}")
        return
    sys.exit(run(fix=args.command == "sync"))


if __name__ == "__main__":
    main()
