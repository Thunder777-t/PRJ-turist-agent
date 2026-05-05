import {
  MIN_RELEVANCE_SCORE,
  normalizeSearchRelevance,
  parseSearchRelevanceList,
  type SearchRelevance,
} from "../../schemas/searchRelevance.schema";
import type { SearchResult } from "../../schemas/searchResult.schema";
import type { SearchTask } from "../../schemas/searchTask.schema";
import type { LlmJsonClient } from "./_llm";
import { toJsonText } from "./_llm";

export interface GradeSearchResultsInput {
  user_input: string;
  conversation_context?: unknown;
  task: SearchTask;
  search_results: SearchResult[];
  llm: LlmJsonClient;
  min_relevance_score?: number;
}

export interface GradeSearchResultsOutput {
  graded_results: SearchRelevance[];
  high_relevance_results: SearchRelevance[];
  discarded_results: SearchRelevance[];
  is_insufficient: boolean;
}

const SYSTEM_PROMPT = `You are a strict relevance grader for travel search results.
Score each result against the assigned search task and user need.
Output strict JSON: {"relevance": [...]}.

Rules:
1) Score range: 0-100.
2) Penalize off-topic, ad-heavy, duplicated, or generic listicles.
3) Real-time task requires timely/official evidence.
4) Include matched_points and missing_points for each row.
5) Do not fabricate facts not present in result payload.`;

function compactResults(results: SearchResult[]): Array<Record<string, string>> {
  return results.map((row) => ({
    url: row.url,
    title: row.title,
    domain: row.domain,
    snippet: row.snippet.slice(0, 280),
    content_excerpt: row.content_excerpt.slice(0, 1200),
    published_at: row.published_at ?? "",
  }));
}

export async function gradeSearchResults(
  input: GradeSearchResultsInput,
): Promise<GradeSearchResultsOutput> {
  const threshold = input.min_relevance_score ?? MIN_RELEVANCE_SCORE;
  const compactPayload = {
    user_input: input.user_input,
    conversation_context: input.conversation_context ?? [],
    task: input.task,
    results: compactResults(input.search_results),
  };

  const llmOutput = await input.llm.generateJson<{ relevance?: unknown }>({
    systemPrompt: SYSTEM_PROMPT,
    userPrompt: toJsonText(compactPayload),
    temperature: 0,
    maxTokens: 2600,
  });

  const parsed = parseSearchRelevanceList(llmOutput);
  const relevanceByUrl = new Map(parsed.map((row) => [row.url, row]));

  const graded: SearchRelevance[] = input.search_results.map((result) => {
    const row = relevanceByUrl.get(result.url);
    if (row) {
      return normalizeSearchRelevance({
        ...row,
        task_id: row.task_id || input.task.task_id,
        url: row.url || result.url,
        title: row.title || result.title,
      });
    }

    return normalizeSearchRelevance({
      task_id: input.task.task_id,
      url: result.url,
      title: result.title,
      relevance_score: 0,
      reasoning: "No LLM grading returned for this result",
      matched_points: [],
      missing_points: ["No reliable relevance judgement"],
    });
  });

  const high = graded.filter((row) => row.relevance_score >= threshold);
  const discarded = graded.filter((row) => row.relevance_score < threshold);

  return {
    graded_results: graded,
    high_relevance_results: high,
    discarded_results: discarded,
    is_insufficient: high.length === 0,
  };
}
