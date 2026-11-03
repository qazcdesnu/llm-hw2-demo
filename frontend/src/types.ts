/**
 * API 응답 타입. backend/app/schemas.py와 맞춘다.
 */

export type Role = "user" | "assistant";

export const DONT_CARE = "dont_care";

export interface Message {
  role: Role;
  content: string;
}

export interface Option {
  value: string;
  label: string;
}

export interface Question {
  attribute: string;
  label: string;
  text: string;
  options: Option[];
}

export interface Answer {
  attribute: string;
  value: string;
}

export interface Recommendation {
  product_id: string;
  title: string;
  probability: number;
  price: number | null;
  image_url: string | null;
  reasons: string[];
}

export interface QuestionScore {
  attribute: string;
  label: string;
  expected_ig: number;
}

export interface Preferences {
  explicit?: Record<string, string>;
  dont_care?: string[];
  asked?: string[];
  excluded?: string[];
  // 구간 속성의 숫자 범위 (예: price_band → [10, 25]). null은 그쪽 한계 없음
  ranges?: Record<string, [number | null, number | null]>;
  // 여러 값 중 아무거나 괜찮다는 조건 (예: form_factor → ["on_ear", "over_ear"])
  any_of?: Record<string, string[]>;
}

export interface MemoryFact {
  attribute: string;
  value: string;
  quote: string;
  turn: number;
  source: "said" | "button";
  low?: number | null; // value가 "range"일 때 숫자 범위
  high?: number | null;
  values?: string[] | null; // value가 "any_of"일 때 괜찮은 값들
}

export interface QuestionRecord {
  attribute: string;
  attempts: number;
  status: "pending" | "answered" | "unanswered" | "skipped";
}

export interface Memory {
  facts?: MemoryFact[];
  notes?: string[];
  questions?: QuestionRecord[];
}

export interface DebugInfo {
  turn: number;
  selected_attribute: string | null;
  expected_ig: number | null;
  entropy: number | null;
  stop_reason: string | null;
  question_scores: QuestionScore[];
  top_candidates: Recommendation[];
  preferences: Preferences;
  request_type: string | null;
  memory: Memory;
  question_source: "llm" | "template" | null;
  question_approach: string | null;
  tie_order: string | null;
  model: string | null;
  confirm_round?: number | null; // 추천 전 확인 질문이면 몇 번째인지
}

// 입력창에서 고를 수 있는 OpenAI 모델 (GET /api/models)
export interface ModelChoice {
  id: string;
  input_price: number | null; // 1M 토큰당 달러
  output_price: number | null;
}

export interface ModelList {
  default: string;
  options: ModelChoice[];
}

export interface ChatResponse {
  reply: string;
  question: Question | null;
  recommendations: Recommendation[];
  debug: DebugInfo;
  finished: boolean;
}

export interface SessionDetail {
  session_id: string;
  messages: Message[];
  question: Question | null;
  finished: boolean;
}

// ---------------------------------------------------------------- 평가 (backend/app/api/evaluation.py)

export interface Scenario {
  id: string;
  brief: string;
  persona: string[];
  opening: string;
  query: string;
  review: string;
}

export interface ManualResult {
  scenario_id: string;
  session_id: string;
  target: { id: string; title: string | null; price: number | null; image_url: string | null; description: string | null };
  recommended: { id: string; title: string }[];
  rank_pct: number | null; // 정답 순위 백분율 (상위 %, 작을수록 좋다)
  attribute_match: number | null;
  naturalness: number;
  satisfaction: number;
  comment: string;
  turns: number;
  created_at?: string;
}

export interface SystemSummary {
  episodes: number;
  errors: number;
  recommended: number;
  rank_pct_mean: number | null;
  rank_pct_median: number | null;
  rank_pct_mean_no_text: number | null;
  rank_pct_median_no_text: number | null;
  attribute_match: number | null;
  questions: number | null;
  confirms: number | null; // 추천 전 확인 질문 수
  fit: number | null;
  naturalness: number | null;
  efficiency: number | null;
  seconds: number | null;
}

export interface Episode {
  scenario_id: string;
  system: string;
  target_id: string;
  recommended: string[];
  rank_pct?: number | null;
  questions?: number;
  attribute_match?: number | null;
  judge?: { fit: number; naturalness: number; efficiency: number; comment: string };
  seconds?: number;
  transcript?: Message[];
  extra?: Record<string, unknown>;
  error?: string;
}

export interface EvalRun {
  id: number;
  created_at: string;
  status: "running" | "done" | "failed";
  config: { systems: string[]; scenario_ids: string[] };
  done: number;
  total: number;
  summary: Record<string, SystemSummary> | null;
  error: string | null;
  episodes?: Episode[];
}
