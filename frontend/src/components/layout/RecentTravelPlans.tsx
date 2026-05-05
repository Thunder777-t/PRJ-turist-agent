import type { Conversation } from "../../types";

type RecentTravelPlansProps = {
  items: Conversation[];
  selectedConversationId: string | null;
  onSelect: (conversationId: string) => void;
  title: string;
  emptyText: string;
};

export default function RecentTravelPlans({
  items,
  selectedConversationId,
  onSelect,
  title,
  emptyText,
}: RecentTravelPlansProps) {
  return (
    <section className="recent-plan-section">
      <div className="recent-plan-title">{title}</div>
      {!items.length ? (
        <div className="recent-plan-empty">{emptyText}</div>
      ) : (
        <div className="recent-plan-list">
          {items.map((item) => (
            <button
              key={`recent-plan-${item.id}`}
              type="button"
              className={`recent-plan-item ${selectedConversationId === item.id ? "active" : ""}`}
              onClick={() => onSelect(item.id)}
            >
              <span>{item.title}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
