import type { TravelPlanPayload } from "./types";

type TravelPlanOverviewProps = {
  plan: TravelPlanPayload;
  language: "zh" | "en";
};

const LABELS = {
  zh: {
    cardTitle: "行程总览",
    destination: "目的地",
    days: "天数",
    budget: "预算估算",
    pace: "旅行节奏",
    assumptions: "默认假设",
    unknown: "待确认",
    noAssumptions: "暂无默认假设",
  },
  en: {
    cardTitle: "Plan Overview",
    destination: "Destination",
    days: "Days",
    budget: "Budget",
    pace: "Pace",
    assumptions: "Default Assumptions",
    unknown: "TBD",
    noAssumptions: "No assumptions yet",
  },
} as const;

function formatDuration(days: number | null, language: "zh" | "en", unknown: string): string {
  if (!days || days <= 0) {
    return unknown;
  }
  return language === "zh" ? `${days} 天` : `${days} days`;
}

export default function TravelPlanOverview({ plan, language }: TravelPlanOverviewProps) {
  const text = LABELS[language];
  return (
    <section className="travel-plan-card" aria-label={text.cardTitle}>
      <div className="travel-plan-card-header">
        <h4>{text.cardTitle}</h4>
      </div>

      <div className="travel-plan-overview-grid">
        <div className="travel-plan-kv">
          <span className="travel-plan-kv-label">{text.destination}</span>
          <span className="travel-plan-kv-value">{plan.destination || text.unknown}</span>
        </div>
        <div className="travel-plan-kv">
          <span className="travel-plan-kv-label">{text.days}</span>
          <span className="travel-plan-kv-value">{formatDuration(plan.durationDays, language, text.unknown)}</span>
        </div>
        <div className="travel-plan-kv">
          <span className="travel-plan-kv-label">{text.budget}</span>
          <span className="travel-plan-kv-value">{plan.budget.totalRange || text.unknown}</span>
        </div>
        <div className="travel-plan-kv">
          <span className="travel-plan-kv-label">{text.pace}</span>
          <span className="travel-plan-kv-value">{plan.pace || text.unknown}</span>
        </div>
      </div>

      <div className="travel-plan-subsection">
        <div className="travel-plan-subtitle">{text.assumptions}</div>
        {plan.assumptions.length ? (
          <ul className="travel-plan-list">
            {plan.assumptions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <div className="travel-plan-empty-inline">{text.noAssumptions}</div>
        )}
      </div>
    </section>
  );
}
