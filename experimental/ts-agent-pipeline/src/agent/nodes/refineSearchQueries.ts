import type { SearchQuery } from "../../schemas/searchQuery.schema";
import { dedupeQueries, normalizeSearchQuery } from "../../schemas/searchQuery.schema";
import { MIN_RELEVANCE_SCORE, type SearchRelevance } from "../../schemas/searchRelevance.schema";
import type { SearchResult } from "../../schemas/searchResult.schema";
import type { SearchTask } from "../../schemas/searchTask.schema";
import { gradeSearchResults } from "./gradeSearchResults";
import type { LlmJsonClient } from "./_llm";
import { toJsonText } from "./_llm";
import { WebSearchTool } from "../../tools/search/webSearchTool";

export interface RelevantSearchResult extends SearchResult {
  relevance_score: number;
  relevance_reasoning: string;
}

export interface RefineSearchQueriesInput {
  user_input: string;
  conversation_context?: unknown;
  task: SearchTask;
  initial_queries: SearchQuery[];
  llm: LlmJsonClient;
  search_tool?: WebSearchTool;
  min_high_relevance_results?: number;
  min_relevance_score?: number;
  max_research_rounds?: number;
}

export interface RefineSearchQueriesOutput {
  task_id: string;
  rounds_executed: number;
  used_queries: SearchQuery[];
  graded_results: SearchRelevance[];
  high_relevance_results: RelevantSearchResult[];
  discarded_count: number;
  is_information_insufficient: boolean;
}

const SYSTEM_PROMPT = `You rewrite precision web search queries for a single travel search task.
Output strict JSON: {"queries": [{"query": string, "rationale": string, "freshness_required": "low|medium|high"}]}

Rules:
1) Improve precision using failure reasons and evidence gaps.
2) Do not repeat ineffective or broad queries.
3) Do not copy user utterance as-is.
4) Keep 2-4 refined queries only.`;

function packFailedRowsForPrompt(
  results: SearchResult[],
  relevance: SearchRelevance[],
): Array<Record<string, string | number>> {
  const byUrl = new Map(relevance.map((row) => [row.url, row]));
  return results.slice(0, 12).map((row) => {
    const grade = byUrl.get(row.url);
    return {
      title: row.title,
      url: row.url,
      domain: row.domain,
      relevance_score: grade?.relevance_score ?? 0,
      reasoning: grade?.reasoning ?? "",
      snippet: row.snippet.slice(0, 200),
    };
  });
}

function parseRefinedQueries(
  payload: unknown,
  task: SearchTask,
  round: number,
  userInput: string,
): SearchQuery[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const raw = (payload as { queries?: unknown }).queries;
  if (!Array.isArray(raw)) {
    return [];
  }

  const normalized = raw
    .map((item) => normalizeSearchQuery(item as Partial<SearchQuery>, task.task_id, round))
    .filter((item) => item.query.length > 0)
    .filter((item) => {
      const normalizedQuery = item.query.trim().toLowerCase();
      const normalizedUser = userInput.trim().toLowerCase();
      if (normalizedQuery === normalizedUser) {
        return false;
      }
      if (normalizedUser.length >= 10 && normalizedQuery.includes(normalizedUser)) {
        return false;
      }
      return true;
    });

  return dedupeQueries(normalized);
}

function mergeRelevantRows(
  results: SearchResult[],
  relevanceRows: SearchRelevance[],
  threshold: number,
): RelevantSearchResult[] {
  const byUrl = new Map(relevanceRows.map((row) => [row.url, row]));
  const merged: RelevantSearchResult[] = [];

  for (const result of results) {
    const relevance = byUrl.get(result.url);
    if (!relevance || relevance.relevance_score < threshold) {
      continue;
    }
    merged.push({
      ...result,
      relevance_score: relevance.relevance_score,
      relevance_reasoning: relevance.reasoning,
    });
  }

  const unique = new Map<string, RelevantSearchResult>();
  for (const row of merged) {
    const existing = unique.get(row.url);
    if (!existing || row.relevance_score > existing.relevance_score) {
      unique.set(row.url, row);
    }
  }
  return Array.from(unique.values()).sort((a, b) => b.relevance_score - a.relevance_score);
}

async function buildRefinedQueries(
  input: RefineSearchQueriesInput,
  round: number,
  triedQueries: SearchQuery[],
  recentResults: SearchResult[],
  recentGrades: SearchRelevance[],
): Promise<SearchQuery[]> {
  const promptPayload = {
    user_input: input.user_input,
    task: input.task,
    already_tried_queries: triedQueries.map((item) => item.query),
    failed_or_low_relevance_results: packFailedRowsForPrompt(recentResults, recentGrades),
  };

  const llmOutput = await input.llm.generateJson<{ queries?: unknown }>({
    systemPrompt: SYSTEM_PROMPT,
    userPrompt: toJsonText(promptPayload),
    temperature: 0.2,
    maxTokens: 1800,
  });

  const refined = parseRefinedQueries(llmOutput, input.task, round, input.user_input);
  const tried = new Set(triedQueries.map((item) => item.query.toLowerCase()));
  return refined.filter((item) => !tried.has(item.query.toLowerCase()));
}

export async function refineSearchQueries(
  input: RefineSearchQueriesInput,
): Promise<RefineSearchQueriesOutput> {
  const threshold = input.min_relevance_score ?? MIN_RELEVANCE_SCORE;
  const minHigh = Math.max(1, input.min_high_relevance_results ?? 3);
  const maxResearchRounds = Math.max(0, Math.min(2, input.max_research_rounds ?? 2));
  const searchTool = input.search_tool ?? new WebSearchTool();

  let round = 0;
  let currentQueries = dedupeQueries(input.initial_queries);
  const usedQueries: SearchQuery[] = [];
  const allGrades: SearchRelevance[] = [];
  const allRelevant = new Map<string, RelevantSearchResult>();

  while (true) {
    usedQueries.push(...currentQueries);

    const results = await searchTool.runTaskSearch(input.task, currentQueries, {
      per_query_limit: 8,
      max_pages_fetch: 36,
      max_results_per_task: 45,
    });

    const grading = await gradeSearchResults({
      user_input: input.user_input,
      conversation_context: input.conversation_context,
      task: input.task,
      search_results: results,
      llm: input.llm,
      min_relevance_score: threshold,
    });

    allGrades.push(...grading.graded_results);

    const relevantRows = mergeRelevantRows(results, grading.graded_results, threshold);
    for (const row of relevantRows) {
      const existing = allRelevant.get(row.url);
      if (!existing || row.relevance_score > existing.relevance_score) {
        allRelevant.set(row.url, row);
      }
    }

    if (allRelevant.size >= minHigh) {
      break;
    }

    if (round >= maxResearchRounds) {
      break;
    }

    const refined = await buildRefinedQueries(
      input,
      round + 1,
      usedQueries,
      results,
      grading.discarded_results,
    );

    if (refined.length === 0) {
      break;
    }

    currentQueries = refined;
    round += 1;
  }

  const finalRelevant = Array.from(allRelevant.values()).sort(
    (a, b) => b.relevance_score - a.relevance_score,
  );

  const dedupedUsedQueries = dedupeQueries(usedQueries);

  return {
    task_id: input.task.task_id,
    rounds_executed: round,
    used_queries: dedupedUsedQueries,
    graded_results: allGrades,
    high_relevance_results: finalRelevant,
    discarded_count: allGrades.filter((row) => row.relevance_score < threshold).length,
    is_information_insufficient: finalRelevant.length < minHigh,
  };
}
