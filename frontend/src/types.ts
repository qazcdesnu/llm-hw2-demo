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
}

export interface MemoryFact {
  attribute: string;
  value: string;
  quote: string;
  turn: number;
  source: "said" | "button";
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
  target_rank: number | null;
  hit1: boolean;
  hit3: boolean;
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
  hit1: number | null;
  hit3: number | null;
  mrr: number | null;
  median_rank: number | null;
  median_rank_no_text: number | null;
  hit3_no_text: number | null;
  attribute_match: number | null;
  questions: number | null;
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
  target_rank?: number | null;
  questions?: number;
  hit1?: boolean;
  hit3?: boolean;
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
