export type FreshnessRequired = "low" | "medium" | "high";
export type SearchPriority = "low" | "medium" | "high";

export type SearchTaskType =
  | "attraction"
  | "restaurant"
  | "hotel_area"
  | "transport"
  | "weather"
  | "guide"
  | "ticketing"
  | "opening_hours"
  | "destination_recommendation"
  | "other";

export interface SearchTask {
  task_id: string;
  information_need: string;
  why_needed: string;
  type: SearchTaskType;
  priority: SearchPriority;
  freshness_required: FreshnessRequired;
  acceptance_criteria: string[];
  destination?: string;
  constraints?: string[];
}

const TASK_TYPES: SearchTaskType[] = [
  "attraction",
  "restaurant",
  "hotel_area",
  "transport",
  "weather",
  "guide",
  "ticketing",
  "opening_hours",
  "destination_recommendation",
  "other",
];

const PRIORITIES: SearchPriority[] = ["low", "medium", "high"];
const FRESHNESS_LEVELS: FreshnessRequired[] = ["low", "medium", "high"];

const REALTIME_TASK_TYPES = new Set<SearchTaskType>([
  "transport",
  "weather",
  "ticketing",
  "opening_hours",
]);

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.trim() : fallback;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter((item) => item.length > 0);
}

function normalizeTaskType(value: unknown): SearchTaskType {
  const text = asString(value, "other");
  return TASK_TYPES.includes(text as SearchTaskType) ? (text as SearchTaskType) : "other";
}

function normalizePriority(value: unknown): SearchPriority {
  const text = asString(value, "medium");
  return PRIORITIES.includes(text as SearchPriority) ? (text as SearchPriority) : "medium";
}

function normalizeFreshness(value: unknown, type: SearchTaskType): FreshnessRequired {
  if (REALTIME_TASK_TYPES.has(type)) {
    return "high";
  }
  const text = asString(value, "medium");
  return FRESHNESS_LEVELS.includes(text as FreshnessRequired)
    ? (text as FreshnessRequired)
    : "medium";
}

export function normalizeSearchTask(task: Partial<SearchTask>, index = 0): SearchTask {
  const type = normalizeTaskType(task.type);
  return {
    task_id: asString(task.task_id, `task_${index + 1}`),
    information_need: asString(task.information_need, "Need more travel evidence"),
    why_needed: asString(task.why_needed, "Needed to answer user request"),
    type,
    priority: normalizePriority(task.priority),
    freshness_required: normalizeFreshness(task.freshness_required, type),
    acceptance_criteria: asStringArray(task.acceptance_criteria),
    destination: asString(task.destination) || undefined,
    constraints: asStringArray(task.constraints),
  };
}

export function parseSearchTaskList(payload: unknown): SearchTask[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const rawTasks = (payload as { search_tasks?: unknown }).search_tasks;
  if (!Array.isArray(rawTasks)) {
    return [];
  }
  return rawTasks.map((task, index) => normalizeSearchTask(task as Partial<SearchTask>, index));
}

export function shouldRequireHighFreshness(task: SearchTask): boolean {
  return REALTIME_TASK_TYPES.has(task.type);
}
