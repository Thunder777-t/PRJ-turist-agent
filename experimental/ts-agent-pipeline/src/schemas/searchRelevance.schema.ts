export interface SearchRelevance {
  task_id: string;
  url: string;
  title: string;
  relevance_score: number;
  reasoning: string;
  matched_points: string[];
  missing_points: string[];
  should_keep: boolean;
}

const KEEP_THRESHOLD = 70;

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

function asScore(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, Math.min(100, Math.round(value)));
  }
  return 0;
}

export function normalizeSearchRelevance(input: Partial<SearchRelevance>): SearchRelevance {
  const score = asScore(input.relevance_score);
  return {
    task_id: asString(input.task_id),
    url: asString(input.url),
    title: asString(input.title),
    relevance_score: score,
    reasoning: asString(input.reasoning),
    matched_points: asStringArray(input.matched_points),
    missing_points: asStringArray(input.missing_points),
    should_keep: score >= KEEP_THRESHOLD,
  };
}

export function parseSearchRelevanceList(payload: unknown): SearchRelevance[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const raw = (payload as { relevance?: unknown }).relevance;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.map((item) => normalizeSearchRelevance(item as Partial<SearchRelevance>));
}

export function keepHighRelevance(items: SearchRelevance[], threshold = KEEP_THRESHOLD): SearchRelevance[] {
  return items.filter((item) => item.relevance_score >= threshold);
}

export const MIN_RELEVANCE_SCORE = KEEP_THRESHOLD;
