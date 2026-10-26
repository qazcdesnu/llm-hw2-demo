import { useEffect, useRef, useState, type FormEvent } from "react";
import { DONT_CARE, type Answer, type Message, type Question, type Recommendation } from "../types";

interface Props {
  messages: Message[];
  question: Question | null;
  recommendations: Recommendation[];
  finished: boolean;
  pending: boolean;
  disabled: boolean;
  error: string | null;
  onSend: (text: string, answer?: Answer) => void;
  onReset: () => void;
}

const START_MESSAGE = "추천해 주세요";
// 추천 후 목록을 조정할 때 쓸 만한 예시 (누르면 그대로 보낸다)
const REFINE_SUGGESTIONS = ["더 저렴한 걸로", "노이즈 캔슬링 있는 걸로", "1번 말고 다른 걸로"];

function formatPrice(price: number | null): string {
  return price == null ? "" : `$${price.toFixed(2)}`;
}

export default function ChatView({
  messages,
  question,
  recommendations,
  finished,
  pending,
  disabled,
  error,
  onSend,
  onReset,
}: Props) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending, question, recommendations]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || pending) return;
    onSend(text);
    setInput("");
  }

  return (
    <section className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="placeholder">
            <p>원하는 헤드폰이나 이어폰을 찾아드릴게요. 몇 가지 질문에 답해 주세요.</p>
            <button
              className="primary"
              onClick={() => onSend(START_MESSAGE)}
              disabled={disabled || pending}
            >
              추천 시작하기
            </button>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.content}
          </div>
        ))}
        {pending && <div className="bubble assistant pending">…</div>}

        {question && !pending && (
          <div className="options" role="group" aria-label={question.label}>
            {question.options.map((o) => (
              <button
                key={o.value}
                className={o.value === DONT_CARE ? "option secondary" : "option"}
                onClick={() => onSend(o.label, { attribute: question.attribute, value: o.value })}
                disabled={disabled}
              >
                {o.label}
              </button>
            ))}
          </div>
        )}

        {finished && recommendations.length > 0 && (
          <ol className="recommendations">
            {recommendations.map((r) => (
              <li key={r.product_id} className="product">
                {r.image_url && <img src={r.image_url} alt="" loading="lazy" />}
                <div className="product-body">
                  <a
                    href={`https://www.amazon.com/dp/${r.product_id}`}
                    target="_blank"
                    rel="noreferrer"
                    className="product-title"
                  >
                    {r.title}
                  </a>
                  <div className="product-price">{formatPrice(r.price)}</div>
                  <ul className="reasons">
                    {r.reasons.map((reason, i) => (
                      <li key={i} className={reason.includes("다름") ? "violated" : undefined}>
                        {reason}
                      </li>
                    ))}
                  </ul>
                </div>
              </li>
            ))}
          </ol>
        )}

        {finished && !pending && (
          <div className="options">
            {REFINE_SUGGESTIONS.map((text) => (
              <button key={text} className="option" onClick={() => onSend(text)} disabled={disabled}>
                {text}
              </button>
            ))}
            <button className="option secondary" onClick={onReset}>
              새 대화로 다시 찾기
            </button>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <div ref={bottomRef} />
      </div>
      <form className="composer" onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            question
              ? "보기를 누르거나 직접 입력하세요"
              : finished
                ? "더 고려할 점이나 바꿀 조건을 말해 주세요"
                : "원하는 조건을 자유롭게 말해 주세요 (예: 출퇴근용 무선 이어폰)"
          }
          disabled={disabled}
          maxLength={2000}
        />
        <button type="submit" disabled={disabled || pending || !input.trim()}>
          전송
        </button>
      </form>
    </section>
  );
}
