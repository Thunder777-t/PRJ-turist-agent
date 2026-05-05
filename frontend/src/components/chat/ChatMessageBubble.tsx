import type { Message } from "../../types";
import type { TravelPlanPayload } from "../travel-plan/types";
import MarkdownMessage from "./MarkdownMessage";
import TravelPlanCards from "../travel-plan/TravelPlanCards";
import ThinkingTraceCard from "./ThinkingTraceCard";
import type { ThinkingTrace } from "./thinkingTypes";

type ChatMessageBubbleProps = {
  message: Message;
  isStreamingAssistant: boolean;
  timeLabel: string;
  language: "zh" | "en";
  travelPlan?: TravelPlanPayload | null;
  thinkingTrace?: ThinkingTrace | null;
  labels: {
    userRole: string;
    assistantRole: string;
    thinking: string;
  };
};

export default function ChatMessageBubble({
  message,
  isStreamingAssistant,
  timeLabel,
  language,
  travelPlan = null,
  thinkingTrace = null,
  labels,
}: ChatMessageBubbleProps) {
  const isUser = message.role === "user";
  const emptyAssistant = !isUser && isStreamingAssistant && !message.content;

  return (
    <article className={`bubble ${isUser ? "user" : "assistant"}`}>
      <div className="bubble-role">{isUser ? labels.userRole : labels.assistantRole}</div>
      <div className="bubble-content">
        {isUser ? (
          <div className="user-message-text">{message.content}</div>
        ) : emptyAssistant ? (
          <>
            {thinkingTrace ? <ThinkingTraceCard trace={thinkingTrace} language={language} /> : null}
            <div className="bubble-loading">{labels.thinking}</div>
          </>
        ) : (
          <>
            {thinkingTrace ? <ThinkingTraceCard trace={thinkingTrace} language={language} /> : null}
            {travelPlan ? (
              <TravelPlanCards
                plan={travelPlan}
                language={language}
              />
            ) : null}
            {message.content.trim() ? <MarkdownMessage content={message.content} /> : null}
          </>
        )}
      </div>
      <div className="bubble-time">{timeLabel}</div>
    </article>
  );
}
