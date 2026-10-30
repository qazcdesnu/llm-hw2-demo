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
  inferred?: Record<string, { value: string; confidence: number; reason: string }>;
  dont_care?: string[];
  asked?: string[];
  excluded?: string[];
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
