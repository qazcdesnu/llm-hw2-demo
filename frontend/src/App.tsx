import { useEffect, useState } from "react";
import { ApiError, createSession, getSession, sendMessage } from "./api";
import ChatView from "./components/ChatView";
import DebugPanel from "./components/DebugPanel";
import EvalPanel from "./components/EvalPanel";
import type { Answer, ChatResponse, Message, Question, Recommendation, Scenario } from "./types";

const SESSION_KEY = "sessionId";

function loadStoredSession(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function storeSession(id: string) {
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    // 저장 실패 시 새로고침하면 새 세션으로 시작한다.
  }
}

async function startNewSession(): Promise<string> {
  const id = await createSession();
  storeSession(id);
  return id;
}

/**
 * @see docs/architecture.md#1-한눈에-보기
 */
export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState<Question | null>(null);
  const [finished, setFinished] = useState(false);
  const [last, setLast] = useState<ChatResponse | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Developer Panel: 디버그 / 평가 모드
  const [panel, setPanel] = useState<"debug" | "eval">("debug");
  // 매뉴얼 평가에서 연기 중인 시나리오 (새 대화를 시작하면 해제)
  const [scenario, setScenario] = useState<Scenario | null>(null);

  useEffect(() => {
    // StrictMode의 이중 실행에서 먼저 끝난 쪽이 상태를 덮어쓰지 않도록 한다.
    let cancelled = false;
    (async () => {
      const stored = loadStoredSession();
      if (stored) {
        try {
          const detail = await getSession(stored);
          if (cancelled) return;
          setMessages(detail.messages);
          setQuestion(detail.question);
          setFinished(detail.finished);
          setSessionId(stored);
          return;
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404)) throw e;
        }
      }
      if (cancelled) return;
      const id = await createSession();
      if (cancelled) return;
      storeSession(id);
      setSessionId(id);
    })().catch((e) => {
      if (!cancelled) setError(String(e));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSend(text: string, answer?: Answer) {
    if (!sessionId) return;
    setMessages((m) => [...m, { role: "user", content: text }]);
    setQuestion(null);
    setPending(true);
    setError(null);
    try {
      const res = await sendMessage(sessionId, text, answer);
      setMessages((m) => [...m, { role: "assistant", content: res.reply }]);
      setQuestion(res.question);
      setFinished(res.finished);
      if (res.recommendations.length > 0) setRecommendations(res.recommendations);
      setLast(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  async function handleReset(next: Scenario | null = null) {
    setError(null);
    setScenario(next);
    try {
      setSessionId(await startNewSession());
      setMessages([]);
      setQuestion(null);
      setFinished(false);
      setLast(null);
      setRecommendations([]);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="layout">
      <header className="header">
        <h1>RecommandShopping</h1>
        <button onClick={() => handleReset()} disabled={pending}>
          새 대화
        </button>
      </header>
      <main className="main">
        <ChatView
          messages={messages}
          question={question}
          recommendations={recommendations}
          finished={finished}
          pending={pending}
          disabled={!sessionId}
          error={error}
          onSend={handleSend}
          onReset={() => handleReset()}
        />
        <aside className="debug">
          <div className="panel-head">
            <h2>Developer Panel</h2>
            <div className="tabs">
              <button className={panel === "debug" ? "tab active" : "tab"} onClick={() => setPanel("debug")}>
                디버그
              </button>
              <button className={panel === "eval" ? "tab active" : "tab"} onClick={() => setPanel("eval")}>
                평가 모드
              </button>
            </div>
          </div>
          {scenario && panel === "debug" && (
            <p className="muted">평가 중인 시나리오: {scenario.id} (평가 모드에서 채점)</p>
          )}
          {panel === "debug" ? (
            <DebugPanel debug={last?.debug ?? null} />
          ) : (
            <EvalPanel
              sessionId={sessionId}
              finished={finished}
              scenario={scenario}
              pending={pending}
              onStartScenario={(s) => handleReset(s)}
              onSend={(text) => handleSend(text)}
            />
          )}
        </aside>
      </main>
    </div>
  );
}
