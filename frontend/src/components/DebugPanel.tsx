import type { DebugInfo } from "../types";

const STOP_REASONS: Record<string, string> = {
  budget: "질문 예산 소진",
  confident: "Top-3 확률이 충분히 모임",
  no_informative_question: "더 물어도 구별 불가",
};

/**
 * Developer Panel: 엔진 내부 상태(질문별 기대 IG, 엔트로피, 상위 후보 확률, 수집된 선호),
 * 대화 메모리(속성 값과 근거 발화, 사용 맥락), 질문 작성기가 고른 접근을 보여준다.
 *
 * @docid ui.debug-panel
 * @see docs/architecture.md#1-한눈에-보기
 */
export default function DebugPanel({ debug }: { debug: DebugInfo | null }) {
  if (!debug) {
    return <p className="muted">아직 대화가 없습니다.</p>;
  }

  const maxIg = Math.max(...debug.question_scores.map((s) => s.expected_ig), 1e-9);
  const explicit = Object.entries(debug.preferences.explicit ?? {});
  const dontCare = debug.preferences.dont_care ?? [];
  const excluded = debug.preferences.excluded ?? [];
  const facts = debug.memory?.facts ?? [];
  const notes = debug.memory?.notes ?? [];
  const questions = debug.memory?.questions ?? [];

  return (
    <div>
      <dl>
        <dt>Turn</dt>
        <dd>{debug.turn}</dd>
        <dt>Request type</dt>
        <dd>
          <code>{debug.request_type ?? "-"}</code>
          {debug.request_type == null && <span className="muted"> (버튼 답 또는 LLM 미사용)</span>}
        </dd>
        <dt>Entropy</dt>
        <dd>{debug.entropy != null ? `${debug.entropy.toFixed(2)} bits` : "-"}</dd>
        {debug.stop_reason && (
          <>
            <dt>Stop reason</dt>
            <dd>
              <code>{debug.stop_reason}</code> {STOP_REASONS[debug.stop_reason] ?? ""}
            </dd>
          </>
        )}
        {debug.question_scores.length > 0 && (
          <>
            <dt>Expected IG</dt>
            <dd>
              <table className="ig-table">
                <tbody>
                  {debug.question_scores.map((s) => (
                    <tr
                      key={s.attribute}
                      className={s.attribute === debug.selected_attribute ? "selected" : undefined}
                    >
                      <td>{s.label}</td>
                      <td className="ig-bar">
                        <span style={{ width: `${(100 * s.expected_ig) / maxIg}%` }} />
                      </td>
                      <td className="num">{s.expected_ig.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </dd>
          </>
        )}
        {debug.question_source && (
          <>
            <dt>Question</dt>
            <dd>
              <code>{debug.question_source}</code>
              {debug.question_approach && <div className="muted">{debug.question_approach}</div>}
            </dd>
          </>
        )}
        <dt>Memory</dt>
        <dd>
          {facts.length + notes.length === 0 ? (
            "-"
          ) : (
            <ul className="prefs">
              {facts.map((f, i) => (
                <li key={i}>
                  <span className="muted">t{f.turn}</span> {f.attribute}=<b>{f.value}</b>{" "}
                  <span className="muted">
                    ← {f.source === "button" ? "버튼" : ""} "{f.quote}"
                  </span>
                </li>
              ))}
              {notes.map((n) => (
                <li key={n} className="muted">
                  맥락: {n}
                </li>
              ))}
            </ul>
          )}
        </dd>
        {questions.length > 0 && (
          <>
            <dt>Questions</dt>
            <dd>
              <ul className="prefs">
                {questions.map((q) => (
                  <li key={q.attribute} className={q.status === "answered" ? undefined : "muted"}>
                    {q.attribute}: {q.status} ({q.attempts}회)
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        <dt>Preferences</dt>
        <dd>
          {explicit.length + dontCare.length + excluded.length === 0 ? (
            "-"
          ) : (
            <ul className="prefs">
              {explicit.map(([k, v]) => (
                <li key={k}>
                  {k}=<b>{v}</b>
                </li>
              ))}
              {dontCare.map((k) => (
                <li key={k} className="muted">
                  {k}=상관없음
                </li>
              ))}
              {excluded.length > 0 && <li className="muted">제외: {excluded.join(", ")}</li>}
            </ul>
          )}
        </dd>
        <dt>Top candidates</dt>
        <dd>
          {debug.top_candidates.length === 0 ? (
            "-"
          ) : (
            <ol>
              {debug.top_candidates.map((c) => (
                <li key={c.product_id}>
                  {c.title.slice(0, 50)}{" "}
                  <span className="muted">({(c.probability * 100).toFixed(1)}%)</span>
                </li>
              ))}
            </ol>
          )}
        </dd>
      </dl>
    </div>
  );
}
