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


def test_symbol_links_do_not_use_line_numbers(tmp_path):
    mod = write(tmp_path / "pkg/mod.py", FUNC)
    md = write(tmp_path / "doc.md", '[helper](pkg/mod.py#L2 "sym:py:pkg.mod#helper")\n')

    # 줄 번호가 붙은 주소는 고쳐야 할 대상이다
    problems, _ = sync(md, tmp_path, fix=False)
    assert "오래된 위치" in problems[0].message

    sync(md, tmp_path)
    assert md.read_text() == '[helper](pkg/mod.py "sym:py:pkg.mod#helper")\n'

    # 함수 위에 코드가 늘어나도 문서는 바뀌지 않는다
    write(mod, "import os\nimport re\n\n" + FUNC)
    assert sync(md, tmp_path, fix=False) == ([], [])


def test_plain_line_number_link_to_code_is_an_error(tmp_path):
    write(tmp_path / "pkg/mod.py", FUNC)
    md = write(tmp_path / "doc.md", "[helper](pkg/mod.py#L2)\n")
    problems, _ = sync(md, tmp_path, fix=False)
    assert problems[0].level == "error" and "줄 번호" in problems[0].message


def test_sync_relocates_symbol_moved_to_another_module(tmp_path):
    write(tmp_path / "pkg/old.py", "")
    write(tmp_path / "pkg/new.py", "X = 1\n" + FUNC)
    md = write(tmp_path / "doc.md", '[helper](pkg/old.py#L2 "sym:py:pkg.old#helper")\n')

    problems, _ = sync(md, tmp_path, fix=False)
    assert "심볼이 이동함" in problems[0].message

    sync(md, tmp_path)
    assert '[helper](pkg/new.py "sym:py:pkg.new#helper")' in md.read_text()


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
    assert '[helper](pkg/new.py "docid:pkg.helper")' in md.read_text()


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
    assert panel.doc_span is not None and panel.style == "js-doc"
    # 파일 머리 주석(빈 줄로 분리)은 모듈 주석으로만 잡힌다.
    assert by_ref["sym:ts:src/types"].doc_span is not None
    assert by_ref["sym:ts:src/types#Role"].doc_span is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node 필요")
def test_repository_links_are_consistent():
    """저장소 전체: 링크·앵커·심볼·백틱 언급이 유효하고, 코드 @see가 문서 링크의 역참조와 정확히 같다."""
    problems, _ = doclinks.collect_problems(doclinks.build_index())
    errors = [str(p) for p in problems if p.level == "error"]
    assert errors == [], "\n".join(errors) + "\n→ tools/doclinks/doclinks.py sync 실행"


# ---------------------------------------------------------------- 양방향 (역참조)

MOD = '''"""모듈 설명."""

# 한도
LIMIT = 3


def helper():
    """도움 함수."""
    return 1
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """REPO를 임시 디렉터리로 바꾼 작은 저장소: backend/app/mod.py + docs/."""
    monkeypatch.setattr(doclinks, "REPO", tmp_path)
    write(tmp_path / "backend/app/__init__.py", "")
    write(tmp_path / "backend/app/mod.py", MOD)
    return tmp_path


def index_repo(root):
    return index_python(root / "backend", ["app"])


def backref_state(root):
    symbols = index_repo(root)
    expected = doclinks.expected_backrefs(symbols, Resolver(symbols))
    return symbols, expected, doclinks.check_backrefs(symbols, expected)


def sync_code(root):
    symbols, expected, _ = backref_state(root)
    return doclinks.apply_backrefs(symbols, expected)


def test_doc_link_requires_backref_and_sync_adds_it(repo):
    write(repo / "docs/guide.md", '# 안내\n\n## 사용법\n\n[helper](../backend/app/mod.py "sym:py:app.mod#helper")\n')
    _, expected, problems = backref_state(repo)
    assert expected == {"sym:py:app.mod#helper": ["docs/guide.md#사용법"]}
    assert "빠짐" in problems[0].message

    sync_code(repo)
    assert '"""도움 함수.\n\n    @see docs/guide.md#사용법\n    """' in (repo / "backend/app/mod.py").read_text()
    assert backref_state(repo)[2] == []


def test_removed_doc_link_removes_backref_and_restores_code(repo):
    doc = write(repo / "docs/guide.md", '## 사용법\n[helper](../backend/app/mod.py "sym:py:app.mod#helper")\n')
    sync_code(repo)
    write(doc, "## 사용법\n링크 없음\n")
    assert "남음" in backref_state(repo)[2][0].message
    sync_code(repo)
    assert (repo / "backend/app/mod.py").read_text() == MOD


def test_backrefs_are_per_section_and_sorted(repo):
    write(repo / "docs/b.md", '## 둘\n[h](../backend/app/mod.py "sym:py:app.mod#helper")\n'
                              '## 하나\n[h](../backend/app/mod.py "sym:py:app.mod#helper")\n')
    write(repo / "docs/a.md", '[h](../backend/app/mod.py "sym:py:app.mod#helper")\n')
    assert backref_state(repo)[1]["sym:py:app.mod#helper"] == ["docs/a.md", "docs/b.md#둘", "docs/b.md#하나"]


def test_one_way_docs_do_not_create_backrefs(repo):
    write(repo / "docs/done.md", '[h](../backend/app/mod.py "sym:py:app.mod#helper")\n')
    assert backref_state(repo)[1] == {}


def test_file_link_maps_to_module_and_round_trips(repo):
    write(repo / "backend/app/bare.py", "import os\n")
    doc = write(repo / "docs/guide.md", "## 파일\n[bare](../backend/app/bare.py)\n")
    sync_code(repo)
    assert (repo / "backend/app/bare.py").read_text() == '"""@see docs/guide.md#파일"""\n\nimport os\n'
    write(doc, "## 파일\n")
    sync_code(repo)
    assert (repo / "backend/app/bare.py").read_text() == "import os\n"


def test_variable_backref_is_a_comment_and_keeps_anchor(repo):
    write(repo / "docs/guide.md", '## 값\n[LIMIT](../backend/app/mod.py "sym:py:app.mod#LIMIT")\n')
    sync_code(repo)
    assert "# 한도\n# @see docs/guide.md#값\nLIMIT = 3" in (repo / "backend/app/mod.py").read_text()
    limit = next(s for s in index_repo(repo) if s.ref == "sym:py:app.mod#LIMIT")
    assert limit.see == ["docs/guide.md#값"]


def test_heading_anchor_rules():
    heads = doclinks.heading_positions('## 출력 ([`schema_for`](a.py "t"))\n## A\n## A\n```\n## 무시\n```\n')
    assert [a for _, a in heads] == ["출력-schema_for", "a", "a-1"]


def test_backtick_mentions(repo):
    doc = write(repo / "docs/m.md", "`backend/app/mod.py` `mod.py` `app/missing.py` `helper()` "
                                    "`NoSuchClass` `FastAPI.get` `data/raw/x.json`\n")
    problems = doclinks.check_mentions(doc, doclinks.MentionIndex(index_repo(repo), repo))
    assert sorted(p.message.split(":")[1].strip().split()[0] for p in problems) == \
        ["`NoSuchClass`", "`app/missing.py`"]


def test_find_references_lists_doc_links_and_see(repo):
    write(repo / "docs/guide.md", '## 사용법\n[helper](../backend/app/mod.py "sym:py:app.mod#helper")\n')
    sync_code(repo)
    refs = doclinks.find_references([repo / "backend/app/mod.py"], index_repo(repo))
    assert ("docs/guide.md:2", "sym:py:app.mod#helper") in refs
    assert ("docs/guide.md#사용법", "@see in sym:py:app.mod#helper") in refs


def test_render_keeps_text_and_tags():
    from backrefs import render_jsdoc, render_py_docstring
    assert render_py_docstring(['    """요약.', "", "    @docid x.y", '    """'], 4, ["d.md#a"]) == \
        ['    """요약.', "", "    @docid x.y", "    @see d.md#a", '    """']
    assert render_jsdoc(["/** 설명 */"], 0, ["d.md"]) == ["/**", " * 설명", " *", " * @see d.md", " */"]
    assert render_jsdoc(["/**", " * @see old.md", " */"], 0, []) == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node 필요")
def test_backref_rewrite_round_trips_on_repository_copy(tmp_path):
    """실제 코드 사본의 모든 심볼에 @see를 넣었다 빼면 바이트 단위로 원래와 같고, 두 번 적용해도 같다."""
    from collections import defaultdict

    import backrefs

    for src in ["backend/app", "backend/scripts", "frontend/src"]:
        shutil.copytree(doclinks.REPO / src, tmp_path / src)
    files = [p for p in tmp_path.rglob("*") if p.suffix in {".py", ".ts", ".tsx"}]
    original = {p: p.read_bytes() for p in files}

    def apply(fn):
        symbols = (index_python(tmp_path / "backend", ["app", "scripts"])
                   + doclinks.index_typescript(tmp_path / "frontend", ["src"]))
        by_file = defaultdict(list)
        for s in symbols:
            if s.editable:
                by_file[s.file].append((s, fn(s)))
        for f, targets in by_file.items():
            assert backrefs.rewrite_file(f, targets) == []

    extra = {"docs/zz.md#x", "docs/a.md"}
    apply(lambda s: sorted(set(s.see) | extra))
    added = {p: p.read_bytes() for p in files}
    apply(lambda s: sorted(set(s.see) | extra))
    assert {p: p.read_bytes() for p in files} == added
    apply(lambda s: sorted(set(s.see) - extra))
    assert {p: p.read_bytes() for p in files} == original


# ---------------------------------------------------------------- Claude Code hook

def test_hook_post_reports_errors_and_referencing_docs(repo, monkeypatch):
    import hook

    write(repo / "docs/guide.md", '## 사용법\n[helper](../backend/app/mod.py "sym:py:app.mod#helper")\n')
    monkeypatch.setattr(doclinks, "build_index", lambda: index_repo(repo))
    out = hook.post({"tool_input": {"file_path": str(repo / "backend/app/mod.py")}})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "역참조(@see) 불일치" in context  # 아직 sync 전
    assert "docs/guide.md:2 → sym:py:app.mod#helper" in context

    sync_code(repo)
    context = hook.post({"tool_input": {"file_path": str(repo / "backend/app/mod.py")}})[
        "hookSpecificOutput"]["additionalContext"]
    assert "오류" not in context and "docs/guide.md:2" in context


def test_hook_post_ignores_unrelated_files(repo):
    import hook

    assert hook.post({"tool_input": {"file_path": str(repo / "notes.txt")}}) is None
    assert hook.post({"tool_input": {"file_path": "/elsewhere/x.py"}}) is None
    assert hook.post({}) is None


def test_hook_stop_blocks_once_then_lets_user_know(repo, monkeypatch):
    import hook

    write(repo / "docs/guide.md", '[x](../backend/app/mod.py "sym:py:app.mod#gone")\n')
    monkeypatch.setattr(doclinks, "build_index", lambda: index_repo(repo))
    blocked = hook.stop({})
    assert blocked["decision"] == "block" and "찾을 수 없는 심볼" in blocked["reason"]
    assert "systemMessage" in hook.stop({"stop_hook_active": True})

    write(repo / "docs/guide.md", "링크 없음\n")
    assert hook.stop({}) is None
