export type TravelPlanDay = {
  day: number | null;
  theme: string;
  morning: string;
  afternoon: string;
  evening: string;
  foodRecommendations: string[];
  transportNotes: string[];
  whyThisDayWorks: string;
};

export type HotelAreaSuggestion = {
  area: string;
  pros: string[];
  cons: string[];
  suitableFor: string[];
};

export type FoodRecommendation = {
  cuisineType: string;
  area: string;
  reason: string;
  avoidTips: string[];
};

export type BudgetEstimate = {
  transport: string;
  accommodation: string;
  food: string;
  tickets: string;
  totalRange: string;
};

export type TravelPlanPayload = {
  destination: string;
  durationDays: number | null;
  pace: string;
  assumptions: string[];
  missingInformation: string[];
  days: TravelPlanDay[];
  hotelAreas: HotelAreaSuggestion[];
  foodRecommendations: FoodRecommendation[];
  budget: BudgetEstimate;
  tips: string[];
  followUpQuestions: string[];
};
