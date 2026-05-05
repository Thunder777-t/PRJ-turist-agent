import {
  dedupeQueries,
  normalizeSearchQuery,
  type SearchQuery,
} from "../../schemas/searchQuery.schema";
import type { SearchTask } from "../../schemas/searchTask.schema";
import type { LlmJsonClient } from "./_llm";
import { toJsonText } from "./_llm";

interface TaskQueryBundle {
  task_id: string;
  queries: Array<Partial<SearchQuery>>;
}

export interface RewriteSearchQueriesInput {
  user_input: string;
  conversation_context?: unknown;
  tasks: SearchTask[];
  llm: LlmJsonClient;
  round?: number;
}

export interface RewriteSearchQueriesOutput {
  task_queries: Record<string, SearchQuery[]>;
}

const SYSTEM_PROMPT = `You are a precision search query rewriting agent.
Rewrite multiple specific web queries for each search task.
Output strict JSON with key task_queries.

Rules:
1) Never copy user utterance directly as final query.
2) Create 3-5 diverse precise queries per task.
3) Queries must be natural and searchable, include landmarks/constraints if available.
4) For realtime info, use freshness_required="high".
5) Each query item must contain: query, rationale, freshness_required.`;

function parseTaskBundles(payload: unknown): TaskQueryBundle[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const raw = (payload as { task_queries?: unknown }).task_queries;
  if (!Array.isArray(raw)) {
    return [];
  }

  const bundles: TaskQueryBundle[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const row = item as { task_id?: unknown; queries?: unknown };
    const task_id = typeof row.task_id === "string" ? row.task_id.trim() : "";
    if (!task_id) {
      continue;
    }
    bundles.push({
      task_id,
      queries: Array.isArray(row.queries) ? (row.queries as Array<Partial<SearchQuery>>) : [],
    });
  }
  return bundles;
}

function notDirectUserCopy(query: string, userInput: string): boolean {
  const normalizedQuery = query.trim().toLowerCase();
  const normalizedUser = userInput.trim().toLowerCase();
  if (!normalizedQuery || !normalizedUser) {
    return true;
  }
  if (normalizedQuery === normalizedUser) {
    return false;
  }
  if (normalizedUser.length >= 10 && normalizedQuery.includes(normalizedUser)) {
    return false;
  }
  return true;
}

function fallbackQueries(task: SearchTask, round: number): SearchQuery[] {
  const base = task.information_need;
  const templates = [
    `${base} 官方 信息`,
    `${base} 本地 推荐`,
    `${base} 最新 攻略`,
  ];

  return templates.map((query, idx) =>
    normalizeSearchQuery(
      {
        task_id: task.task_id,
        query,
        rationale: `Fallback query template ${idx + 1}`,
        freshness_required: task.freshness_required,
      },
      task.task_id,
      round,
    ),
  );
}

export async function rewriteSearchQueries(
  input: RewriteSearchQueriesInput,
): Promise<RewriteSearchQueriesOutput> {
  const round = input.round ?? 0;
  const llmPayload = {
    user_input: input.user_input,
    conversation_context: input.conversation_context ?? [],
    search_tasks: input.tasks,
    round,
  };

  const llmOutput = await input.llm.generateJson<{ task_queries?: unknown }>({
    systemPrompt: SYSTEM_PROMPT,
    userPrompt: toJsonText(llmPayload),
    temperature: 0.2,
    maxTokens: 2400,
  });

  const parsedBundles = parseTaskBundles(llmOutput);
  const bundleMap = new Map(parsedBundles.map((bundle) => [bundle.task_id, bundle.queries]));

  const taskQueries: Record<string, SearchQuery[]> = {};
  for (const task of input.tasks) {
    const rawQueries = bundleMap.get(task.task_id) ?? [];
    const normalized = rawQueries
      .map((query) => normalizeSearchQuery(query, task.task_id, round))
      .filter((query) => query.query.length > 0)
      .filter((query) => notDirectUserCopy(query.query, input.user_input));

    const deduped = dedupeQueries(normalized);
    taskQueries[task.task_id] = deduped.length > 0 ? deduped : fallbackQueries(task, round);
  }

  return { task_queries: taskQueries };
}
