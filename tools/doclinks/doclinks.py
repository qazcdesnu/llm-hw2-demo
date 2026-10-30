"""문서 ↔ 코드 양방향 링크 관리 도구.

문서에서 코드를 가리킬 때 줄 번호가 아니라 심볼 ID(객체·함수·클래스)로 연결한다.
링크 주소는 파일 경로만 두고, 심볼이 다른 파일로 옮겨지면 이 도구가 코드 구조(AST)를 보고 경로를 고친다.
줄 번호로 코드를 가리키는 링크(#L10)는 오류로 처리한다.

양방향: 문서 → 코드 링크가 기준이고, 코드의 @see는 그 역참조다. 문서의 어느 절에서 심볼을
링크하면 그 심볼의 docstring·JSDoc·주석에 `@see <문서>#<절>`이 정확히 있어야 한다(빠짐·남음 모두 오류).
@see는 sync가 파서(ast, TS 컴파일러 API)로 찾은 위치에 결정적으로 생성한다.
형식과 규칙: docs/doc-links.md

    uv run --project backend python tools/doclinks/doclinks.py check   # 깨진 링크 검사 (실패 시 exit 1)
    uv run --project backend python tools/doclinks/doclinks.py sync    # 문서 링크 경로 + 코드 @see 역참조 갱신
    uv run --project backend python tools/doclinks/doclinks.py index   # 심볼 목록 출력
    uv run --project backend python tools/doclinks/doclinks.py refs backend/app/core/engine.py
                                                                       # 이 코드를 참조하는 문서 위치

검사 대상:
- 문서 → 문서·코드 링크: 파일·앵커·심볼 존재, 주소가 현재 위치와 같은지, 줄 번호 링크 금지
- 문서의 백틱 언급: `scripts/build_catalog.py` 같은 코드·문서 경로, `IGEngine.step`·`match_text()` 같은 코드 이름
- 코드 → 문서: @see 대상이 존재하는지, 그리고 문서 링크에서 계산한 역참조와 정확히 같은지
- @docid 중복

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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backrefs  # noqa: E402

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

# 백틱 언급 중 존재를 검사하는 경로: git에 포함되는 코드·문서 파일만.
# 데이터 파일(.json, .csv, .db)과 디렉터리는 로컬에만 있을 수 있어 검사하지 않는다.
MENTION_PATH_RE = re.compile(r"^[\w.\-/]+\.(py|ts|tsx|mjs|sh|yaml|yml|md)$")
# 백틱 언급 중 코드 이름으로 보는 것: `name()` 형태이거나 CamelCase로 시작하는 (점으로 이은) 이름
MENTION_SYMBOL_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*(\(\))?$")
CAMEL_RE = re.compile(r"^[A-Z][a-z0-9]+[A-Z]\w*$")
# 우리 코드가 아닌 이름(라이브러리 등). 한 줄에 하나.
EXTERNAL_NAMES_FILE = Path(__file__).with_name("external_names.txt")

# 코드로 가는 링크가 단방향인 문서 (저장소 기준 경로). 작업 기록·계획 문서라 이 링크로는 코드에 @see를 만들지 않는다.
ONE_WAY_DOCS = {"docs/done.md", "docs/ongoing.md", "docs/issue.md", "docs/roadmap.md"}


@dataclass
class Symbol:
    ref: str  # sym:<lang>:<module>[#<qualname>]
    kind: str
    file: Path
    start: int
    end: int
    docid: str | None = None
    see: list[str] = field(default_factory=list)
    # 태그(@docid, @see)를 담는 주석의 줄 범위. 없으면 None
    doc_span: tuple[int, int] | None = None
    # 주석이 없을 때 새 주석을 넣을 줄과 들여쓰기
    insert_line: int = 1
    indent: int = 0
    # py-docstring | py-comment(변수 위 # 주석) | js-doc
    style: str = "py-docstring"
    # 한 줄 정의처럼 주석을 넣을 자리가 없으면 False
    editable: bool = True

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
            lines = source.split("\n")
            tree = ast.parse(source, filename=str(path))
            docid, see = parse_tags(ast.get_docstring(tree))
            ds = _docstring_node(tree.body)
            symbols.append(Symbol(f"sym:py:{module}", "module", path, 1, max(1, source.count("\n")),
                                  docid, see, doc_span=(ds.lineno, ds.end_lineno) if ds else None))
            _walk_python(tree.body, module, "", path, lines, symbols)
    return symbols


def _docstring_node(body: list[ast.stmt]) -> ast.Expr | None:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[0]
    return None


def _first_line(node: ast.stmt) -> int:
    return min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])


def _comment_block(lines: list[str], line: int) -> tuple[int, int] | None:
    """line(1부터) 바로 위에 붙은 # 주석 줄 범위."""
    start, i = None, line - 2
    while i >= 0 and lines[i].strip().startswith("#"):
        start, i = i + 1, i - 1
    return (start, line - 1) if start else None


def _walk_python(body: list[ast.stmt], module: str, prefix: str, path: Path, lines: list[str],
                 out: list[Symbol]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qualname = prefix + node.name
            docid, see = parse_tags(ast.get_docstring(node))
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            sym = Symbol(f"sym:py:{module}#{qualname}", kind, path, _first_line(node), node.end_lineno,
                         docid, see)
            ds = _docstring_node(node.body)
            if ds:
                sym.doc_span, sym.insert_line, sym.indent = (ds.lineno, ds.end_lineno), ds.lineno, ds.col_offset
            else:
                first = _first_line(node.body[0])
                block = _comment_block(lines, first)
                sym.insert_line = block[0] if block else first
                sym.indent = node.body[0].col_offset
                sym.editable = first > node.lineno
            out.append(sym)
            # 함수 내부는 공개 심볼이 아니므로 클래스만 들어간다.
            if isinstance(node, ast.ClassDef):
                _walk_python(node.body, module, qualname + ".", path, lines, out)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            block = _comment_block(lines, node.lineno)
            # 줄 맨 앞의 #만 지운다 (@see 대상의 #앵커는 남겨야 한다)
            comment = "\n".join(re.sub(r"^\s*#", "", line) for line in lines[block[0] - 1:block[1]]) \
                if block else ""
            docid, see = parse_tags(comment)
            for t in targets:
                if isinstance(t, ast.Name):
                    out.append(Symbol(f"sym:py:{module}#{prefix}{t.id}", "variable", path,
                                      node.lineno, node.end_lineno, docid, see, doc_span=block,
                                      insert_line=node.lineno, indent=node.col_offset,
                                      style="py-comment"))


def index_typescript(root: Path = TS_ROOT, dirs: list[str] = TS_DIRS) -> list[Symbol]:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node가 없어 TypeScript 심볼을 읽을 수 없다")
    env = {**os.environ, "TS_MODULE_ROOT": str(TS_ROOT)}  # typescript는 frontend/node_modules에서
    result = subprocess.run([node, str(TS_INDEXER), str(root), *dirs],
                            capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"TypeScript 인덱서 실패 (frontend에서 npm install 필요?):\n{result.stderr}")
    symbols = []
    for raw in json.loads(result.stdout):
        docid, see = parse_tags(raw["comment"])
        span = (raw["docStart"], raw["docEnd"]) if raw.get("docStart") else None
        symbols.append(Symbol(raw["ref"], raw["kind"], Path(raw["file"]), raw["start"], raw["end"],
                              docid, see, doc_span=span, insert_line=raw["insertLine"],
                              indent=raw["indent"], style="js-doc"))
    return symbols


def build_index() -> list[Symbol]:
    return index_python() + index_typescript()


# ---------------------------------------------------------------- Markdown

def markdown_files(root: Path | None = None) -> list[Path]:
    root = root or REPO
    return sorted(p for p in root.rglob("*.md") if not SKIP_DIRS & set(p.relative_to(root).parts))


FENCE_RE = re.compile(r"^```.*?^```[^\n]*$", re.M | re.S)
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.M)


def heading_positions(text: str) -> list[tuple[int, str]]:
    """(위치, 앵커) 목록. GitHub 규칙: 소문자, 문장부호 제거, 공백→-, 같은 제목은 -1, -2 …"""
    fences = [m.span() for m in FENCE_RE.finditer(text)]
    counts: dict[str, int] = {}
    out = []
    for m in HEADING_RE.finditer(text):
        if any(a <= m.start() < b for a, b in fences):
            continue
        # 제목 속 링크는 표시 글자만 남긴다 (GitHub은 렌더링된 글자로 앵커를 만든다)
        title = LINK_RE.sub(lambda lm: lm.group(1), m.group(1))
        title = re.sub(r"<[^>]+>", "", title).replace("`", "")
        slug = re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        out.append((m.start(), slug if n == 0 else f"{slug}-{n}"))
    return out


def heading_anchors(path: Path) -> set[str]:
    """GitHub 규칙의 제목 앵커와 <a id="…"> 앵커."""
    text = path.read_text(encoding="utf-8")
    anchors = set(re.findall(r'<a id="([^"]+)"', FENCE_RE.sub("", text)))
    return anchors | {a for _, a in heading_positions(text)}


def section_at(heads: list[tuple[int, str]], pos: int) -> str | None:
    """pos가 속한 절(바로 앞 제목)의 앵커."""
    anchor = None
    for start, a in heads:
        if start > pos:
            break
        anchor = a
    return anchor


def iter_links(text: str):
    """코드 블록·인라인 코드 밖의 링크 match."""
    code = [m.span() for m in CODE_RE.finditer(text)]
    for m in LINK_RE.finditer(text):
        if not any(s <= m.start() < e for s, e in code):
            yield m


def expected_href(md: Path, symbol: Symbol) -> str:
    """심볼 링크의 주소: 심볼이 있는 파일의 상대 경로. 줄 번호는 넣지 않는다."""
    return Path(os.path.relpath(symbol.file, md.parent)).as_posix()


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
            problems.append(Problem("error", where,
                                    f"줄 번호로 코드를 가리킴: {href} → [이름](경로 \"sym:…\") 형식의 심볼 링크로 바꿀 것"))

    if fix and edits:
        for (start, end), replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        md.write_text(text, encoding="utf-8")
    return problems, changes


# ---------------------------------------------------------------- 백틱 언급

class MentionIndex:
    """문서 본문의 백틱 언급(경로·코드 이름)이 실제로 있는지 판단하는 색인."""

    def __init__(self, symbols: list[Symbol], root: Path | None = None):
        root = root or REPO
        self.names: set[str] = set()
        for s in symbols:
            if s.qualname:
                parts = s.qualname.split(".")
                # Class.method는 "Class.method"와 "method" 둘 다로 찾을 수 있다
                self.names.update(".".join(parts[i:]) for i in range(len(parts)))
        self.external: set[str] = set()
        if EXTERNAL_NAMES_FILE.exists():
            self.external = {line.strip() for line in EXTERNAL_NAMES_FILE.read_text().splitlines()
                             if line.strip() and not line.startswith("#")}
        self.root = root
        self.basenames = {p.name for p in root.rglob("*")
                          if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts)}

    @staticmethod
    def looks_like_symbol(token: str) -> bool:
        base = token.removesuffix("()")
        return token.endswith("()") or bool(CAMEL_RE.match(base.split(".")[0]))

    def symbol_exists(self, token: str) -> bool:
        base = token.removesuffix("()")
        return base in self.names or base in self.external or base.split(".")[0] in self.external

    def path_exists(self, md: Path, token: str) -> bool:
        if "/" not in token:
            return token in self.basenames
        bases = [md.parent, self.root, self.root / "backend", self.root / "frontend"]
        return any((b / token).exists() for b in bases)


def check_mentions(md: Path, index: MentionIndex) -> list[Problem]:
    """코드 블록 밖 인라인 코드(`…`)로 적은 경로·코드 이름이 실제로 있는지."""
    text = md.read_text(encoding="utf-8")
    problems = []
    for m in CODE_RE.finditer(text):
        raw = m.group(0)
        if raw.startswith("```"):
            continue
        token = raw.strip("`").strip()
        where = f"{_rel(md)}:{line_of(text, m.start())}"
        if MENTION_PATH_RE.match(token):
            if not index.path_exists(md, token):
                problems.append(Problem("error", where, f"없는 파일을 언급: `{token}`"))
        elif MENTION_SYMBOL_RE.match(token) and index.looks_like_symbol(token):
            if not index.symbol_exists(token):
                problems.append(Problem(
                    "error", where,
                    f"코드에 없는 이름을 언급: `{token}` (이름이 바뀌었으면 문서를 고치고, "
                    f"외부 라이브러리 이름이면 {EXTERNAL_NAMES_FILE.name}에 추가)"))
    return problems


# ---------------------------------------------------------------- 역참조

def find_references(code_files: list[Path], symbols: list[Symbol]) -> list[tuple[str, str]]:
    """이 코드 파일들의 심볼을 가리키는 문서 링크와 @see. [(문서 위치, 대상)]"""
    targets = {p.resolve() for p in code_files}
    in_files = [s for s in symbols if s.file.resolve() in targets]
    titles = {s.ref: s for s in in_files} | {f"docid:{s.docid}": s for s in in_files if s.docid}
    refs = []
    for md in markdown_files():
        text = md.read_text(encoding="utf-8")
        for m in iter_links(text):
            title = m.group(3)
            if title in titles:
                refs.append((f"{_rel(md)}:{line_of(text, m.start())}", title))
            elif not title and m.group(2).split("#")[0]:
                dest = (md.parent / unquote(m.group(2).split("#")[0])).resolve()
                if dest in targets:
                    refs.append((f"{_rel(md)}:{line_of(text, m.start())}", _rel(dest)))
    for s in in_files:
        for target in s.see:
            refs.append((target, f"@see in {s.ref}"))
    return refs


# ---------------------------------------------------------------- 양방향 (역참조)

def expected_backrefs(symbols: list[Symbol], resolver: "Resolver") -> dict[str, list[str]]:
    """문서 링크에서 계산한 심볼별 역참조 목록 {심볼 ref: ["docs/x.md#절", …]} (정렬됨).

    문서 → 코드 링크(심볼 링크, 또는 코드 파일 링크 = 모듈)마다, 링크가 있는 절을 역참조로 둔다.
    ONE_WAY_DOCS의 링크는 제외한다.

    @docid doclinks.expected-backrefs
    """
    modules = {s.file.resolve(): s for s in symbols if s.kind == "module"}
    out: dict[str, set[str]] = defaultdict(set)
    for md in markdown_files():
        rel = _rel(md)
        if rel in ONE_WAY_DOCS:
            continue
        text = md.read_text(encoding="utf-8")
        heads = heading_positions(text)
        for m in iter_links(text):
            href, title = m.group(2), m.group(3)
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            if title and title.startswith(("sym:", "docid:")):
                sym = resolver.resolve(title)
            else:
                path = href.split("#", 1)[0]
                sym = modules.get((md.parent / unquote(path)).resolve()) if path else None
            if sym is None:
                continue
            anchor = section_at(heads, m.start())
            out[sym.ref].add(f"{rel}#{anchor}" if anchor else rel)
    return {ref: sorted(v) for ref, v in out.items()}


def check_backrefs(symbols: list[Symbol], expected: dict[str, list[str]]) -> list[Problem]:
    problems = []
    for s in symbols:
        want, have = expected.get(s.ref, []), s.see
        if sorted(set(have)) == want and len(have) == len(set(have)):
            continue
        missing = [t for t in want if t not in have]
        extra = sorted({t for t in have if t not in want})
        detail = "; ".join(x for x in [f"빠짐 {missing}" if missing else "",
                                       f"남음 {extra}" if extra else "",
                                       "중복" if len(have) != len(set(have)) else ""] if x)
        problems.append(Problem("error", f"{_rel(s.file)}:{s.start}",
                                f"역참조(@see) 불일치 {s.ref}: {detail} → sync로 갱신"))
    return problems


def apply_backrefs(symbols: list[Symbol], expected: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """코드의 @see를 기대 역참조와 같게 다시 쓴다. (변경 내역, 실패 내역)"""
    by_file: dict[Path, list[tuple[Symbol, list[str]]]] = defaultdict(list)
    for s in symbols:
        want = expected.get(s.ref, [])
        if s.see != want:
            by_file[s.file].append((s, want))
    changes, failures = [], []
    for path, targets in sorted(by_file.items()):
        failures += backrefs.rewrite_file(path, targets)
        changes += [f"{_rel(path)}: {s.ref} @see {s.see} → {want}" for s, want in targets]
    return changes, failures


def collect_problems(symbols: list[Symbol], fix: bool = False) -> tuple[list[Problem], list[str]]:
    """저장소 전체 검사. (문제, sync로 바꾼 문서 링크 내역)"""
    resolver = Resolver(symbols)
    mentions = MentionIndex(symbols)
    problems = list(resolver.problems) + check_code_references(symbols)
    changes: list[str] = []
    for md in markdown_files():
        p, c = process_markdown(md, resolver, fix)
        problems += p + check_mentions(md, mentions)
        changes += c
    problems += check_backrefs(symbols, expected_backrefs(symbols, resolver))
    return problems, changes


def sync_all() -> tuple[list[Problem], list[str], list[str]]:
    """문서 링크 경로 → 코드 @see 순서로 고치고 다시 검사한다. (남은 문제, 변경 내역, 실패 내역)"""
    symbols = build_index()
    _, changes = collect_problems(symbols, fix=True)
    resolver = Resolver(symbols)
    code_changes, failures = apply_backrefs(symbols, expected_backrefs(symbols, resolver))
    problems, _ = collect_problems(build_index())
    problems += [Problem("error", f, "역참조 자동 수정 실패, 수동 수정 필요") for f in failures]
    return problems, changes + code_changes, failures


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
    if fix:
        problems, changes, _ = sync_all()
    else:
        symbols = build_index()
        problems, changes = collect_problems(symbols)
    for c in changes:
        print(f"UPDATED {c}")
    for p in problems:
        print(p)
    errors = sum(p.level == "error" for p in problems)
    print(f"{len(markdown_files())} docs, "
          f"{errors} errors, {len(problems) - errors} warnings, {len(changes)} updates")
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="링크 검사 (문서→코드, 문서→문서, 코드 @see→문서)")
    sub.add_parser("sync", help="문서 링크 경로 갱신·이동한 심볼 추적, 코드 @see 역참조 재생성")
    p_index = sub.add_parser("index", help="심볼 목록")
    p_index.add_argument("--json", action="store_true")
    p_refs = sub.add_parser("refs", help="코드 파일의 심볼을 참조하는 문서 위치")
    p_refs.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    if args.command == "refs":
        refs = find_references(args.files, build_index())
        for where, target in refs:
            print(f"{where}  →  {target}")
        print(f"{len(refs)} references")
        return

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
