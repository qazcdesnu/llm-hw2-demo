import type {
  Answer,
  ChatResponse,
  EvalRun,
  ManualResult,
  Scenario,
  SessionDetail,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    // FastAPI의 {"detail": "..."}가 있으면 그 메시지를 보여준다 (예: 카탈로그 없음).
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // 본문이 JSON이 아니면 상태 코드만 쓴다.
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export async function createSession(): Promise<string> {
  const { session_id } = await request<{ session_id: string }>("/api/sessions", {
    method: "POST",
  });
  return session_id;
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${sessionId}`);
}

/**
 * 메시지를 보내고 한 턴의 결과(응답, 다음 질문, 추천, 디버그 정보)를 받는다.
 * 보기 버튼을 누른 경우 answer를 함께 보내 문자열 해석 없이 그대로 반영한다.
 *
 * @docid ui.send-message
 * @see docs/architecture.md#1-한눈에-보기
 */
export function sendMessage(
  sessionId: string,
  message: string,
  answer?: Answer,
): Promise<ChatResponse> {
  return request<ChatResponse>(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify(answer ? { message, answer } : { message }),
  });
}

// ---------------------------------------------------------------- 평가

export function listScenarios(): Promise<Scenario[]> {
  return request<Scenario[]>("/api/eval/scenarios");
}

export function scoreManual(body: {
  session_id: string;
  scenario_id: string;
  naturalness: number;
  satisfaction: number;
  comment: string;
}): Promise<ManualResult> {
  return request<ManualResult>("/api/eval/manual", { method: "POST", body: JSON.stringify(body) });
}

export function listManual(): Promise<ManualResult[]> {
  return request<ManualResult[]>("/api/eval/manual");
}

export function startRun(systems: string[], limit: number): Promise<{ run_id: number }> {
  return request<{ run_id: number }>("/api/eval/runs", {
    method: "POST",
    body: JSON.stringify({ systems, limit }),
  });
}

export function listRuns(): Promise<EvalRun[]> {
  return request<EvalRun[]>("/api/eval/runs");
}

export function getRun(id: number): Promise<EvalRun> {
  return request<EvalRun>(`/api/eval/runs/${id}`);
}
