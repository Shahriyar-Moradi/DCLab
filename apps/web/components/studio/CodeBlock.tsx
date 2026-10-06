export function CodeBlock({ code, label, language }: { code: string; label: string; language?: string }) {
  return (
    <div className="code-wrap">
      <pre className="code" tabIndex={0} aria-label={label}>
        <code data-language={language}>{code}</code>
      </pre>
    </div>
  );
}
