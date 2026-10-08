import type { FormEvent, ReactNode } from "react";

export type ChatMessage = { id: string; role: "user" | "agent"; from: string; meta?: string; body: ReactNode };

/** Presentational chat: the caller owns state and submission. */
export function Chat({
  messages,
  label,
  composer,
}: {
  messages: ChatMessage[];
  label: string;
  composer?: { value: string; onChange: (value: string) => void; onSubmit: () => void; placeholder?: string; disabled?: boolean };
}) {
  function submit(event: FormEvent) {
    event.preventDefault();
    composer?.onSubmit();
  }
  return (
    <div className="chat">
      <div role="log" aria-label={label} aria-live="polite" className="chat">
        {messages.map((message) => (
          <article key={message.id} className={`msg ${message.role}`}>
            <div className="from">
              <span>{message.from}</span>
              {message.meta ? <span>{message.meta}</span> : null}
            </div>
            <div>{message.body}</div>
          </article>
        ))}
      </div>
      {composer ? (
        <form className="composer" onSubmit={submit}>
          <textarea
            aria-label="Message"
            value={composer.value}
            placeholder={composer.placeholder}
            disabled={composer.disabled}
            onChange={(event) => composer.onChange(event.target.value)}
          />
          <div className="acts">
            <button type="submit" className="btn primary" disabled={composer.disabled || composer.value.trim() === ""}>
              Send
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
