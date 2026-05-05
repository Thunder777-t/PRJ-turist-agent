type ChatEmptyStateProps = {
  prompts: string[];
  onSelectPrompt: (prompt: string) => void;
  title: string;
  subtitle: string;
  ariaLabel: string;
};

export default function ChatEmptyState({
  prompts,
  onSelectPrompt,
  title,
  subtitle,
  ariaLabel,
}: ChatEmptyStateProps) {
  return (
    <section className="chat-empty-state" aria-label={ariaLabel}>
      <div className="chat-empty-head">
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>

      <div className="chat-empty-prompts">
        {prompts.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="chat-empty-prompt-card"
            onClick={() => onSelectPrompt(prompt)}
          >
            {prompt}
          </button>
        ))}
      </div>
    </section>
  );
}
