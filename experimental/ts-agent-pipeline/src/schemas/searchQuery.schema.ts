import type { FreshnessRequired } from "./searchTask.schema";

export interface SearchQuery {
  task_id: string;
  query: string;
  rationale: string;
  freshness_required: FreshnessRequired;
  round: number;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.trim() : fallback;
}

function normalizeFreshness(value: unknown): FreshnessRequired {
  const text = asString(value, "medium");
  if (text === "low" || text === "medium" || text === "high") {
    return text;
  }
  return "medium";
}

export function normalizeSearchQuery(
  query: Partial<SearchQuery>,
  fallbackTaskId: string,
  round: number,
): SearchQuery {
  return {
    task_id: asString(query.task_id, fallbackTaskId),
    query: asString(query.query),
    rationale: asString(query.rationale, "Expand evidence coverage"),
    freshness_required: normalizeFreshness(query.freshness_required),
    round,
  };
}

export function parseSearchQueryList(
  payload: unknown,
  fallbackTaskId: string,
  round: number,
): SearchQuery[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const raw = (payload as { queries?: unknown }).queries;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .map((query) => normalizeSearchQuery(query as Partial<SearchQuery>, fallbackTaskId, round))
    .filter((query) => query.query.length > 0);
}

export function dedupeQueries(queries: SearchQuery[]): SearchQuery[] {
  const seen = new Set<string>();
  const output: SearchQuery[] = [];
  for (const query of queries) {
    const key = query.query.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    output.push(query);
  }
  return output;
}
