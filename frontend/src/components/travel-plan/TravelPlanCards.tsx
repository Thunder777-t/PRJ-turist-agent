import TravelPlanOverview from "./TravelPlanOverview";
import type { TravelPlanPayload } from "./types";

type TravelPlanCardsProps = {
  plan: TravelPlanPayload;
  language: "zh" | "en";
};

export default function TravelPlanCards({ plan, language }: TravelPlanCardsProps) {
  return (
    <div className="travel-plan-layout">
      <TravelPlanOverview plan={plan} language={language} />
    </div>
  );
}
