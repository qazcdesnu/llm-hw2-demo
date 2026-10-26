"""tools/doclinks: 코드가 움직여도 문서 링크가 유지되는지 검증한다."""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "doclinks"))

import doclinks  # noqa: E402
from doclinks import Resolver, index_python, process_markdown  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def sync(md: Path, root: Path, fix: bool = True):
    return process_markdown(md, Resolver(index_python(root, ["pkg"])), fix=fix)


FUNC = '''
def helper():
    """도움 함수.

    @docid pkg.helper
    @see docs/architecture.md
    """
    return 1
'''


def test_python_index_lines_tags_and_nesting(tmp_path):
    write(tmp_path / "pkg/__init__.py", '"""패키지.\n\n@see docs/roadmap.md\n"""\n')
    write(tmp_path / "pkg/mod.py", '''import functools

LIMIT = 3


@functools.cache
def cached():
    return 1


class Box:
    size: int = 0

    def open(self):
        """@docid pkg.box-open"""
''')
    by_ref = {s.ref: s for s in index_python(tmp_path, ["pkg"])}

    assert by_ref["sym:py:pkg"].see == ["docs/roadmap.md"]
    assert by_ref["sym:py:pkg.mod#LIMIT"].start == 3
    assert by_ref["sym:py:pkg.mod#cached"].start == 6  # 데코레이터 줄부터
    assert by_ref["sym:py:pkg.mod#Box.size"].kind == "variable"
    assert by_ref["sym:py:pkg.mod#Box.open"].docid == "pkg.box-open"


def test_sync_follows_line_changes(tmp_path):
    mod = write(tmp_path / "pkg/mod.py", FUNC)
    md = write(tmp_path / "doc.md", '[helper](pkg/mod.py "sym:py:pkg.mod#helper")\n')

    problems, _ = sync(md, tmp_path, fix=False)
    assert "오래된 위치" in problems[0].message

    sync(md, tmp_path)
    assert '(pkg/mod.py#L2 "sym:py:pkg.mod#helper")' in md.read_text()

    # 함수 위에 코드가 늘어나도 sync하면 따라간다.
    write(mod, "import os\nimport re\n\n" + FUNC)
    sync(md, tmp_path)
    assert '(pkg/mod.py#L5 "sym:py:pkg.mod#helper")' in md.read_text()
    assert sync(md, tmp_path, fix=False)[0] == []


def test_sync_relocates_symbol_moved_to_another_module(tmp_path):
    write(tmp_path / "pkg/old.py", "")
    write(tmp_path / "pkg/new.py", "X = 1\n" + FUNC)
    md = write(tmp_path / "doc.md", '[helper](pkg/old.py#L2 "sym:py:pkg.old#helper")\n')

    problems, _ = sync(md, tmp_path, fix=False)
    assert "심볼이 이동함" in problems[0].message

    sync(md, tmp_path)
    assert '[helper](pkg/new.py#L3 "sym:py:pkg.new#helper")' in md.read_text()


def test_ambiguous_move_is_reported_not_guessed(tmp_path):
    write(tmp_path / "pkg/a.py", FUNC.replace("@docid pkg.helper", ""))
    write(tmp_path / "pkg/b.py", FUNC.replace("@docid pkg.helper", ""))
    md = write(tmp_path / "doc.md", '[helper](pkg/old.py "sym:py:pkg.old#helper")\n')

    problems, changes = sync(md, tmp_path)
    assert "찾을 수 없는 심볼" in problems[0].message
    assert changes == []


def test_docid_survives_rename_and_move(tmp_path):
    write(tmp_path / "pkg/new.py", FUNC.replace("def helper", "def renamed_helper"))
    md = write(tmp_path / "doc.md", '[helper](pkg/old.py#L2 "docid:pkg.helper")\n')

    sync(md, tmp_path)
    assert '[helper](pkg/new.py#L2 "docid:pkg.helper")' in md.read_text()


def test_links_inside_code_are_ignored(tmp_path):
    write(tmp_path / "pkg/mod.py", FUNC)
    md = write(tmp_path / "doc.md", '`[x](pkg/mod.py "sym:py:pkg.mod#nope")`\n```\n[y](missing.md)\n```\n')
    assert sync(md, tmp_path, fix=False) == ([], [])


def test_duplicate_docid_is_an_error(tmp_path):
    write(tmp_path / "pkg/a.py", FUNC)
    write(tmp_path / "pkg/b.py", FUNC)
    resolver = Resolver(index_python(tmp_path, ["pkg"]))
    assert "중복" in resolver.problems[0].message


@pytest.mark.skipif(shutil.which("node") is None, reason="node 필요")
def test_typescript_index_of_frontend():
    by_ref = {s.ref: s for s in doclinks.index_typescript()}
    panel = by_ref["sym:ts:src/components/DebugPanel#DebugPanel"]
    assert panel.docid == "ui.debug-panel"
    # 파일 머리 주석(빈 줄로 분리)은 모듈 주석으로만 잡힌다.
    assert by_ref["sym:ts:src/types"].see == ["backend/README.md#api"]
    assert by_ref["sym:ts:src/types#Role"].see == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node 필요")
def test_repository_docs_have_no_broken_links():
    symbols = doclinks.build_index()
    resolver = Resolver(symbols)
    problems = list(resolver.problems) + doclinks.check_code_references(symbols)
    for md in doclinks.markdown_files():
        problems += process_markdown(md, resolver, fix=False)[0]
    errors = [str(p) for p in problems if p.level == "error"]
    assert errors == [], "\n".join(errors) + "\n→ tools/doclinks/doclinks.py sync 실행"
