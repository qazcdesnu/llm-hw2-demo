// TypeScript 심볼 인덱서. doclinks.py가 호출한다.
// TS 컴파일러 API로 선언 위치와 앞쪽 주석(JSDoc)을 뽑아 JSON 배열로 출력한다.
//
//   node ts_symbols.mjs <project-root> <dir> [<dir> ...]
//
// 출력: [{ref, kind, file, start, end, comment, docStart, docEnd, insertLine, indent}]
//   ref: sym:ts:<project-root 기준 경로(확장자 제외)>#<이름 또는 Class.member>
//   comment: 선언 앞 주석 원문 (@docid, @see 태그는 doclinks.py가 파싱)
//   docStart/docEnd: 태그를 담는 JSDoc(/** */)의 줄 범위 (없으면 null)
//   insertLine/indent: JSDoc이 없을 때 새로 넣을 줄과 들여쓰기 (doclinks sync가 @see를 쓸 때 사용)
//
// typescript 모듈은 <project-root>/node_modules에서 찾는다. 다른 위치에서 찾으려면 TS_MODULE_ROOT.

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const [projectRoot, ...dirs] = process.argv.slice(2);
// 프로젝트의 node_modules에 있는 typescript를 쓴다.
const moduleRoot = process.env.TS_MODULE_ROOT || projectRoot;
const ts = createRequire(path.join(path.resolve(moduleRoot), "package.json"))("typescript");

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules") yield* walk(full);
    } else if (/\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts")) {
      yield full;
    }
  }
}

function commentRanges(text, node) {
  return ts.getLeadingCommentRanges(text, node.getFullStart()) ?? [];
}

const joinComments = (text, ranges) => ranges.map((r) => text.slice(r.pos, r.end)).join("\n");

// 태그를 담는 JSDoc: 앞쪽 주석 중 마지막 /** */
const lastJsDoc = (text, ranges) =>
  [...ranges].reverse().find((r) => text.slice(r.pos, r.pos + 3) === "/**") ?? null;

// 파일 첫 선언 앞의 주석 중, 뒤에 빈 줄이 오는 것까지는 파일(모듈) 주석으로 본다.
// Python의 모듈 docstring에 해당한다. 나머지는 첫 선언의 주석이다.
function splitHeader(text, sf) {
  const first = sf.statements[0];
  if (!first) return { header: [], own: [] };
  const ranges = commentRanges(text, first);
  let split = 0;
  ranges.forEach((r, i) => {
    const next = i + 1 < ranges.length ? ranges[i + 1].pos : first.getStart(sf);
    if (/\n\s*\n/.test(text.slice(r.end, next))) split = i + 1;
  });
  return { header: ranges.slice(0, split), own: ranges.slice(split) };
}

const out = [];

for (const dir of dirs) {
  for (const file of walk(path.join(projectRoot, dir))) {
    const text = fs.readFileSync(file, "utf8");
    const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
    const module = path.relative(projectRoot, file).split(path.sep).join("/").replace(/\.tsx?$/, "");
    const line = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;
    const { header, own } = splitHeader(text, sf);

    const docSpan = (ranges) => {
      const doc = lastJsDoc(text, ranges);
      return doc ? { docStart: line(doc.pos), docEnd: line(doc.end) } : { docStart: null, docEnd: null };
    };

    const add = (name, symKind, node, commentNode = node) => {
      const ranges = commentNode === sf.statements[0] ? own : commentRanges(text, commentNode);
      const startPos = commentNode.getStart(sf);
      out.push({
        ref: `sym:ts:${module}#${name}`,
        kind: symKind,
        file: path.resolve(file),
        start: line(node.getStart(sf)),
        end: line(node.getEnd()),
        comment: joinComments(text, ranges),
        ...docSpan(ranges),
        insertLine: line(startPos),
        indent: sf.getLineAndCharacterOfPosition(startPos).character,
      });
    };

    out.push({
      ref: `sym:ts:${module}`,
      kind: "module",
      file: path.resolve(file),
      start: 1,
      end: line(sf.getEnd()),
      comment: joinComments(text, header),
      ...docSpan(header),
      insertLine: 1,
      indent: 0,
    });

    for (const stmt of sf.statements) {
      if (ts.isFunctionDeclaration(stmt)) {
        add(stmt.name ? stmt.name.text : "default", "function", stmt);
      } else if (ts.isClassDeclaration(stmt)) {
        const cls = stmt.name ? stmt.name.text : "default";
        add(cls, "class", stmt);
        for (const member of stmt.members) {
          if (member.name && ts.isIdentifier(member.name)) {
            add(`${cls}.${member.name.text}`, "member", member);
          }
        }
      } else if (ts.isInterfaceDeclaration(stmt)) {
        add(stmt.name.text, "interface", stmt);
      } else if (ts.isTypeAliasDeclaration(stmt)) {
        add(stmt.name.text, "type", stmt);
      } else if (ts.isEnumDeclaration(stmt)) {
        add(stmt.name.text, "enum", stmt);
      } else if (ts.isVariableStatement(stmt)) {
        for (const decl of stmt.declarationList.declarations) {
          if (ts.isIdentifier(decl.name)) add(decl.name.text, "variable", stmt);
        }
      }
    }
  }
}

process.stdout.write(JSON.stringify(out));
