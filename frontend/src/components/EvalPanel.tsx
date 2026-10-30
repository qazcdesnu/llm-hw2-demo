import { useEffect, useState } from "react";
import { getRun, listManual, listRuns, listScenarios, scoreManual, startRun } from "../api";
import type { Episode, EvalRun, ManualResult, Scenario, SystemSummary } from "../types";

const SYSTEMS: { key: string; label: string }[] = [
  { key: "ig", label: "IG (우리 시스템)" },
  { key: "random", label: "Random (질문만 무작위)" },
  { key: "llm", label: "LLM (자유 대화 + 검색)" },
];

const METRICS: { key: keyof SystemSummary; label: string; digits: number }[] = [
  { key: "hit3", label: "Hit@3", digits: 2 },
  { key: "hit1", label: "Hit@1", digits: 2 },
  { key: "mrr", label: "MRR", digits: 3 },
  { key: "median_rank", label: "정답 순위(중앙)", digits: 0 },
  { key: "attribute_match", label: "속성 일치", digits: 2 },
  { key: "fit", label: "적합(심판)", digits: 2 },
  { key: "naturalness", label: "자연스러움", digits: 2 },
  { key: "efficiency", label: "효율", digits: 2 },
  { key: "questions", label: "질문 수", digits: 1 },
  { key: "seconds", label: "초/대화", digits: 0 },
];

function fmt(value: unknown, digits = 2): string {
  if (value == null) return "-";
  if (typeof value === "boolean") return value ? "O" : "X";
  if (typeof value === "number") return value.toFixed(digits);
  return String(value);
}

interface Props {
  sessionId: string | null;
  finished: boolean;
  scenario: Scenario | null;
  pending: boolean;
  onStartScenario: (s: Scenario) => void;
  onSend: (text: string) => void;
}

/**
 * 평가 모드: 매뉴얼 평가(C4 시나리오 역할극 + 사람 점수)와 자동 평가(가상 사용자 × 세 시스템) 결과.
 *
 * @docid ui.eval-panel
 * @see docs/architecture.md#1-한눈에-보기
 */
export default function EvalPanel(props: Props) {
  const [tab, setTab] = useState<"manual" | "auto">("manual");
  return (
    <div className="eval">
      <div className="tabs">
        <button className={tab === "manual" ? "tab active" : "tab"} onClick={() => setTab("manual")}>
          매뉴얼 평가
        </button>
        <button className={tab === "auto" ? "tab active" : "tab"} onClick={() => setTab("auto")}>
          자동 평가
        </button>
      </div>
      {tab === "manual" ? <ManualEval {...props} /> : <AutoEval />}
    </div>
  );
}

// ---------------------------------------------------------------- 매뉴얼 평가

function ManualEval({ sessionId, finished, scenario, pending, onStartScenario, onSend }: Props) {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [naturalness, setNaturalness] = useState(3);
  const [satisfaction, setSatisfaction] = useState(3);
  const [comment, setComment] = useState("");
  const [result, setResult] = useState<ManualResult | null>(null);
  const [history, setHistory] = useState<ManualResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listScenarios().then((s) => {
      setScenarios(s);
      if (s.length > 0) setSelected((cur) => cur || s[0].id);
    }).catch((e) => setError(String(e)));
    listManual().then(setHistory).catch(() => undefined);
  }, []);

  useEffect(() => setResult(null), [scenario, sessionId]);

  const preview = scenarios.find((s) => s.id === selected) ?? null;

  async function submit() {
    if (!sessionId || !scenario) return;
    setError(null);
    try {
      const r = await scoreManual({ session_id: sessionId, scenario_id: scenario.id, naturalness, satisfaction, comment });
      setResult(r);
      setHistory((h) => [r, ...h]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (scenarios.length === 0) {
    return <p className="muted">{error ?? "시나리오가 없습니다. backend에서 scripts/build_scenarios.py를 실행하세요."}</p>;
  }

  return (
    <div>
      <p className="muted">
        C4 실제 구매자의 상황을 연기해서 대화하세요. 추천을 받으면 점수를 매기고, 정답(실제 구매 상품) 순위를 확인합니다.
      </p>
      {!scenario && (
        <>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} className="wide">
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id} · {s.brief.slice(0, 40)}
              </option>
            ))}
          </select>
          {preview && <ScenarioCard scenario={preview} />}
          {preview && (
            <button className="primary" onClick={() => onStartScenario(preview)}>
              이 시나리오로 새 대화 시작
            </button>
          )}
        </>
      )}

      {scenario && (
        <>
          <ScenarioCard scenario={scenario} />
          {!finished && (
            <button onClick={() => onSend(scenario.opening)} disabled={pending}>
              첫 마디 보내기: “{scenario.opening}”
            </button>
          )}
          {finished && !result && (
            <div className="score-form">
              <label>
                대화 자연스러움
                <Stars value={naturalness} onChange={setNaturalness} />
              </label>
              <label>
                추천 만족도
                <Stars value={satisfaction} onChange={setSatisfaction} />
              </label>
              <textarea placeholder="메모 (선택)" value={comment} onChange={(e) => setComment(e.target.value)} />
              <button className="primary" onClick={submit}>
                채점하고 정답 보기
              </button>
            </div>
          )}
          {result && <ManualResultCard result={result} />}
          <button className="secondary" onClick={() => onStartScenario(scenario)}>
            이 시나리오 다시 하기
          </button>
        </>
      )}
      {error && <p className="error">{error}</p>}

      {history.length > 0 && (
        <>
          <h3>지난 매뉴얼 평가</h3>
          <table className="eval-table">
            <thead>
              <tr>
                <th>시나리오</th>
                <th>Hit@3</th>
                <th>정답 순위</th>
                <th>자연</th>
                <th>만족</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h, i) => (
                <tr key={i}>
                  <td>{h.scenario_id}</td>
                  <td>{fmt(h.hit3)}</td>
                  <td>{fmt(h.target_rank, 0)}</td>
                  <td>{h.naturalness}</td>
                  <td>{h.satisfaction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <span className="stars">
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} className={n <= value ? "star on" : "star"} onClick={() => onChange(n)} type="button">
          {n}
        </button>
      ))}
    </span>
  );
}

function ScenarioCard({ scenario }: { scenario: Scenario }) {
  return (
    <div className="card">
      <b>{scenario.brief}</b>
      <ul>
        {scenario.persona.map((p, i) => (
          <li key={i}>{p}</li>
        ))}
      </ul>
      <details>
        <summary className="muted">C4 원문 (질의·리뷰)</summary>
        <p className="muted">{scenario.query}</p>
        <p className="muted">{scenario.review}</p>
      </details>
    </div>
  );
}

function ManualResultCard({ result }: { result: ManualResult }) {
  return (
    <div className="card">
      <b>
        정답 순위: {fmt(result.target_rank, 0)}위 · Hit@3 {fmt(result.hit3)} · 1위 속성 일치{" "}
        {fmt(result.attribute_match)}
      </b>
      <div className="target">
        {result.target.image_url && <img src={result.target.image_url} alt="" />}
        <div>
          <div className="muted">실제 구매 상품</div>
          <a href={`https://www.amazon.com/dp/${result.target.id}`} target="_blank" rel="noreferrer">
            {result.target.title}
          </a>
          <div className="muted">{result.target.description}</div>
        </div>
      </div>
      <div className="muted">추천받은 상품</div>
      <ol>
        {result.recommended.map((r) => (
          <li key={r.id} className={r.id === result.target.id ? "hit" : undefined}>
            {r.title.slice(0, 70)}
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- 자동 평가

function AutoEval() {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [systems, setSystems] = useState<string[]>(SYSTEMS.map((s) => s.key));
  const [limit, setLimit] = useState(10);
  const [openRun, setOpenRun] = useState<EvalRun | null>(null);
  const [openEpisode, setOpenEpisode] = useState<Episode | null>(null);
  const [error, setError] = useState<string | null>(null);

  const running = runs.some((r) => r.status === "running");

  useEffect(() => {
    let alive = true;
    const load = () => listRuns().then((r) => alive && setRuns(r)).catch((e) => alive && setError(String(e)));
    load();
    const timer = setInterval(() => running && load(), 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [running]);

  async function run() {
    const n = systems.length * limit;
    if (!window.confirm(`대화 ${n}개를 실제 LLM으로 돌립니다 (대략 $${(n * 0.02).toFixed(2)}, 수 분). 진행할까요?`)) return;
    setError(null);
    try {
      await startRun(systems, limit);
      setRuns(await listRuns());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function open(id: number) {
    setOpenEpisode(null);
    setOpenRun(await getRun(id));
  }

  return (
    <div>
      <p className="muted">
        C4 시나리오마다 가상 사용자(LLM)가 세 시스템과 각각 대화합니다. 정답은 실제 구매 상품, 대화 품질은 LLM 심판이
        채점합니다.
      </p>
      <div className="run-form">
        {SYSTEMS.map((s) => (
          <label key={s.key}>
            <input
              type="checkbox"
              checked={systems.includes(s.key)}
              onChange={(e) =>
                setSystems((cur) => (e.target.checked ? [...cur, s.key] : cur.filter((x) => x !== s.key)))
              }
            />
            {s.label}
          </label>
        ))}
        <label>
          시나리오 수{" "}
          <input type="number" min={1} max={200} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
        </label>
        <button className="primary" onClick={run} disabled={running || systems.length === 0}>
          {running ? "실행 중…" : "평가 실행"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      {runs.map((r) => (
        <div key={r.id} className="card">
          <div>
            <b>run {r.id}</b> <span className="muted">{r.created_at} · {r.status} · {r.done}/{r.total}</span>{" "}
            <button className="link" onClick={() => open(r.id)}>
              에피소드 보기
            </button>
          </div>
          {r.summary && <SummaryTable summary={r.summary} />}
          {r.error && <pre className="error">{r.error.slice(-400)}</pre>}
        </div>
      ))}

      {openRun?.episodes && (
        <div className="card">
          <b>run {openRun.id} 에피소드</b>
          <table className="eval-table">
            <thead>
              <tr>
                <th>시나리오</th>
                <th>시스템</th>
                <th>Hit@3</th>
                <th>순위</th>
                <th>적합</th>
                <th>자연</th>
                <th>질문</th>
              </tr>
            </thead>
            <tbody>
              {openRun.episodes.map((e, i) => (
                <tr key={i} onClick={() => setOpenEpisode(e)} className="clickable">
                  <td>{e.scenario_id}</td>
                  <td>{e.system}</td>
                  <td>{e.error ? "오류" : fmt(e.hit3)}</td>
                  <td>{fmt(e.target_rank, 0)}</td>
                  <td>{fmt(e.judge?.fit, 0)}</td>
                  <td>{fmt(e.judge?.naturalness, 0)}</td>
                  <td>{fmt(e.questions, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openEpisode && (
        <div className="card">
          <b>
            {openEpisode.scenario_id} · {openEpisode.system}
          </b>
          {openEpisode.error && <p className="error">{openEpisode.error}</p>}
          {openEpisode.judge && <p className="muted">심판: {openEpisode.judge.comment}</p>}
          <div className="transcript">
            {openEpisode.transcript?.map((m, i) => (
              <div key={i} className={`line ${m.role}`}>
                <b>{m.role === "user" ? "가상 사용자" : openEpisode.system}</b> {m.content}
              </div>
            ))}
          </div>
          <div className="muted">
            정답 {openEpisode.target_id} / 추천 {openEpisode.recommended.join(", ") || "없음"}
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryTable({ summary }: { summary: Record<string, SystemSummary> }) {
  const systems = Object.keys(summary);
  return (
    <table className="eval-table">
      <thead>
        <tr>
          <th />
          {systems.map((s) => (
            <th key={s}>{s}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {METRICS.map((m) => (
          <tr key={m.key}>
            <td>{m.label}</td>
            {systems.map((s) => (
              <td key={s} className="num">
                {fmt(summary[s][m.key], m.digits)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
