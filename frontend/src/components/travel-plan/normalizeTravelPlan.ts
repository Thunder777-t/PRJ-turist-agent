import type {
  BudgetEstimate,
  FoodRecommendation,
  HotelAreaSuggestion,
  TravelPlanDay,
  TravelPlanPayload,
} from "./types";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function asString(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return "";
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const list: string[] = [];
  for (const item of value) {
    if (typeof item === "string") {
      const text = item.trim();
      if (text) {
        list.push(text);
      }
      continue;
    }
    const row = asRecord(item);
    if (!row) {
      continue;
    }
    const field = asString(row.field) || asString(row.name) || asString(row.type) || asString(row.value);
    if (field) {
      list.push(field);
    }
  }
  return list;
}

function parseDuration(raw: unknown): number | null {
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
    return Math.round(raw);
  }
  const text = asString(raw);
  if (!text) {
    return null;
  }
  const match = text.match(/\d+/);
  if (!match) {
    return null;
  }
  const value = Number(match[0]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function pickFirstString(source: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = asString(source[key]);
    if (value) {
      return value;
    }
  }
  return "";
}

function pickFirstList(source: Record<string, unknown>, keys: string[]): string[] {
  for (const key of keys) {
    const list = asStringList(source[key]);
    if (list.length) {
      return list;
    }
  }
  return [];
}

function normalizeDay(raw: unknown): TravelPlanDay | null {
  const row = asRecord(raw);
  if (!row) {
    return null;
  }

  const day =
    typeof row.day === "number" && Number.isFinite(row.day) && row.day > 0
      ? Math.round(row.day)
      : parseDuration(row.day);
  const theme = pickFirstString(row, ["theme", "title"]);
  const morning = pickFirstString(row, ["morning", "am", "morning_plan"]);
  const afternoon = pickFirstString(row, ["afternoon", "pm", "afternoon_plan"]);
  const evening = pickFirstString(row, ["evening", "night", "evening_plan"]);
  const foodRecommendations = pickFirstList(row, [
    "food_recommendations",
    "foodRecommendations",
    "food",
  ]);
  const transportNotes = pickFirstList(row, ["transport_notes", "transportNotes", "transportation", "traffic"]);
  const whyThisDayWorks = pickFirstString(row, ["why_this_day_works", "whyThisDayWorks", "reason"]);

  if (!theme && !morning && !afternoon && !evening) {
    return null;
  }

  return {
    day: day ?? null,
    theme,
    morning,
    afternoon,
    evening,
    foodRecommendations,
    transportNotes,
    whyThisDayWorks,
  };
}

function normalizeHotelArea(raw: unknown): HotelAreaSuggestion | null {
  if (typeof raw === "string") {
    const area = raw.trim();
    if (!area) {
      return null;
    }
    return { area, pros: [], cons: [], suitableFor: [] };
  }

  const row = asRecord(raw);
  if (!row) {
    return null;
  }

  const area = pickFirstString(row, ["area", "name", "district", "region"]);
  const pros = pickFirstList(row, ["pros", "advantages", "highlights", "strengths"]);
  const cons = pickFirstList(row, ["cons", "disadvantages", "weaknesses"]);
  const suitableFor = pickFirstList(row, ["suitable_for", "suitableFor", "for", "target"]);

  if (!area && !pros.length && !cons.length && !suitableFor.length) {
    return null;
  }

  return {
    area: area || "TBD area",
    pros,
    cons,
    suitableFor,
  };
}

function normalizeFoodRecommendation(raw: unknown): FoodRecommendation | null {
  if (typeof raw === "string") {
    const cuisineType = raw.trim();
    if (!cuisineType) {
      return null;
    }
    return {
      cuisineType,
      area: "",
      reason: "",
      avoidTips: [],
    };
  }

  const row = asRecord(raw);
  if (!row) {
    return null;
  }

  const cuisineType = pickFirstString(row, ["cuisine_type", "cuisineType", "type", "name", "dish"]);
  const area = pickFirstString(row, ["area", "region", "district"]);
  const reason = pickFirstString(row, ["reason", "why", "description"]);
  const avoidTips = pickFirstList(row, ["avoid_tips", "avoidTips", "warnings"]);

  if (!cuisineType && !area && !reason && !avoidTips.length) {
    return null;
  }

  return {
    cuisineType: cuisineType || "Local recommendation",
    area,
    reason,
    avoidTips,
  };
}

function normalizeBudget(raw: unknown): BudgetEstimate {
  const unknownValue = "TBD";
  if (typeof raw === "string") {
    const text = raw.trim();
    if (text) {
      return {
        transport: unknownValue,
        accommodation: unknownValue,
        food: unknownValue,
        tickets: unknownValue,
        totalRange: text,
      };
    }
  }

  const row = asRecord(raw);
  if (!row) {
    return {
      transport: unknownValue,
      accommodation: unknownValue,
      food: unknownValue,
      tickets: unknownValue,
      totalRange: unknownValue,
    };
  }

  const transport = pickFirstString(row, ["transport", "transportation", "traffic"]);
  const accommodation = pickFirstString(row, ["accommodation", "hotel", "stay", "lodging"]);
  const food = pickFirstString(row, ["food", "meals", "dining"]);
  const tickets = pickFirstString(row, ["tickets", "ticket", "attractions", "sightseeing"]);
  const totalRange = pickFirstString(row, [
    "total_range",
    "totalRange",
    "total",
    "range",
    "estimate",
    "per_person",
  ]);

  return {
    transport: transport || unknownValue,
    accommodation: accommodation || unknownValue,
    food: food || unknownValue,
    tickets: tickets || unknownValue,
    totalRange: totalRange || unknownValue,
  };
}

function parseTopLevelPlan(payload: unknown): Record<string, unknown> | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }

  const candidates: Array<Record<string, unknown>> = [
    root,
    asRecord(root.json),
    asRecord(root.structured_response),
    asRecord(root.data),
  ].filter((item): item is Record<string, unknown> => Boolean(item));

  for (const candidate of candidates) {
    const travelPlan = asRecord(candidate.travelPlan) ?? asRecord(candidate.travel_plan);
    if (travelPlan) {
      return travelPlan;
    }

    const frontend = asRecord(candidate.frontend_json);
    if (frontend) {
      return frontend;
    }

    const hasPlanSignals =
      typeof candidate.destination === "string" ||
      typeof candidate.duration_days === "number" ||
      Array.isArray(candidate.itinerary);
    if (hasPlanSignals) {
      return candidate;
    }
  }

  return null;
}

function deriveFoodRecommendationsFromDays(days: TravelPlanDay[]): FoodRecommendation[] {
  const list: FoodRecommendation[] = [];
  const seen = new Set<string>();
  for (const dayItem of days) {
    for (const food of dayItem.foodRecommendations) {
      const key = food.toLowerCase();
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      list.push({
        cuisineType: food,
        area: "",
        reason: dayItem.day ? `Appears in Day ${dayItem.day}` : "From daily itinerary",
        avoidTips: [],
      });
      if (list.length >= 12) {
        return list;
      }
    }
  }
  return list;
}

export function parseTravelPlanFromPayload(payload: unknown): TravelPlanPayload | null {
  const plan = parseTopLevelPlan(payload);
  if (!plan) {
    return null;
  }

  const destination = pickFirstString(plan, ["destination", "city", "target_destination"]);
  const durationDays = parseDuration(plan.duration_days ?? plan.durationDays ?? plan.days_count);
  const pace = pickFirstString(plan, ["travel_pace", "pace", "rhythm", "style"]);
  const assumptions = pickFirstList(plan, ["assumptions", "default_assumptions"]);

  const missingInformationRaw = plan.missing_information ?? plan.missingInformation;
  const missingInformation = asStringList(missingInformationRaw);

  const itineraryRaw = Array.isArray(plan.itinerary)
    ? plan.itinerary
    : Array.isArray(plan.day_itinerary)
      ? plan.day_itinerary
      : Array.isArray(plan.days)
        ? plan.days
        : [];
  const days: TravelPlanDay[] = itineraryRaw
    .map((item) => normalizeDay(item))
    .filter((item): item is TravelPlanDay => Boolean(item));

  const hotelRaw = Array.isArray(plan.hotel_area_suggestions)
    ? plan.hotel_area_suggestions
    : Array.isArray(plan.hotelAreas)
      ? plan.hotelAreas
      : [];
  const hotelAreas = hotelRaw
    .map((item) => normalizeHotelArea(item))
    .filter((item): item is HotelAreaSuggestion => Boolean(item));

  const foodRaw = Array.isArray(plan.food_recommendations)
    ? plan.food_recommendations
    : Array.isArray(plan.foodRecommendations)
      ? plan.foodRecommendations
      : [];
  let foodRecommendations = foodRaw
    .map((item) => normalizeFoodRecommendation(item))
    .filter((item): item is FoodRecommendation => Boolean(item));
  if (!foodRecommendations.length) {
    foodRecommendations = deriveFoodRecommendationsFromDays(days);
  }

  const budget = normalizeBudget(plan.budget_estimate ?? plan.budgetEstimate);
  const tips = asStringList(plan.tips);
  const followUpQuestions = asStringList(plan.follow_up_questions ?? plan.followUpQuestions);

  if (!destination && !durationDays && !days.length && !followUpQuestions.length) {
    return null;
  }

  return {
    destination: destination || "TBD destination",
    durationDays,
    pace: pace || "TBD pace",
    assumptions,
    missingInformation,
    days,
    hotelAreas,
    foodRecommendations,
    budget,
    tips,
    followUpQuestions,
  };
}
