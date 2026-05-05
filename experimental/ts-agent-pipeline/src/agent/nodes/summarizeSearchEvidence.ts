import {
  normalizeSearchEvidence,
  type SearchEvidence,
  type EvidenceCitation,
} from "../../schemas/searchEvidence.schema";
import type { SearchTask } from "../../schemas/searchTask.schema";
import type { LlmJsonClient } from "./_llm";
import { toJsonText } from "./_llm";
import type { RelevantSearchResult } from "./refineSearchQueries";

export interface SummarizeSearchEvidenceTaskInput {
  task: SearchTask;
  high_relevance_results: RelevantSearchResult[];
  is_information_insufficient?: boolean;
}

export interface SummarizeSearchEvidenceInput {
  user_input: string;
  conversation_context?: unknown;
  tasks: SummarizeSearchEvidenceTaskInput[];
  llm: LlmJsonClient;
}

const SYSTEM_PROMPT = `You are a travel evidence summarization agent.
Summarize evidence ONLY from provided high-relevance results.
Output strict JSON matching this shape:
{
  "task_id": string,
  "information_need": string,
  "evidence_summary": string,
  "citations": [{"title": string, "url": string, "domain": string, "relevance_score": number}],
  "confidence": number,
  "unresolved_gaps": string[],
  "is_information_insufficient": boolean
}

Rules:
1) Do not use any source outside provided results.
2) If evidence is weak or missing, explicitly set is_information_insufficient=true.
3) Keep citations factual and traceable.`;

function fallbackEvidence(task: SearchTask): SearchEvidence {
  return normalizeSearchEvidence({
    task_id: task.task_id,
    information_need: task.information_need,
    evidence_summary: "当前高相关证据不足，无法给出可靠结论。",
    citations: [],
    confidence: 20,
    unresolved_gaps: ["高相关来源不足，需要补充搜索"],
    is_information_insufficient: true,
  });
}

function ensureCitationCoverage(
  evidence: SearchEvidence,
  results: RelevantSearchResult[],
): SearchEvidence {
  if (evidence.citations.length > 0) {
    return evidence;
  }

  const citations: EvidenceCitation[] = results.slice(0, 4).map((row) => ({
    title: row.title,
    url: row.url,
    domain: row.domain,
    relevance_score: row.relevance_score,
  }));

  return {
    ...evidence,
    citations,
    is_information_insufficient: citations.length === 0 || evidence.is_information_insufficient,
  };
}

function compactResults(results: RelevantSearchResult[]): Array<Record<string, string | number>> {
  return results.map((row) => ({
    title: row.title,
    url: row.url,
    domain: row.domain,
    relevance_score: row.relevance_score,
    snippet: row.snippet.slice(0, 240),
    content_excerpt: row.content_excerpt.slice(0, 1200),
    published_at: row.published_at ?? "",
  }));
}

export async function summarizeSearchEvidence(
  input: SummarizeSearchEvidenceInput,
): Promise<SearchEvidence[]> {
  const outputs: SearchEvidence[] = [];

  for (const taskInput of input.tasks) {
    const { task, high_relevance_results } = taskInput;
    if (high_relevance_results.length === 0 || taskInput.is_information_insufficient) {
      outputs.push(fallbackEvidence(task));
      continue;
    }

    const payload = {
      user_input: input.user_input,
      conversation_context: input.conversation_context ?? [],
      task,
      high_relevance_results: compactResults(high_relevance_results),
    };

    try {
      const llmOutput = await input.llm.generateJson<Partial<SearchEvidence>>({
        systemPrompt: SYSTEM_PROMPT,
        userPrompt: toJsonText(payload),
        temperature: 0.1,
        maxTokens: 1800,
      });

      const normalized = normalizeSearchEvidence({
        ...llmOutput,
        task_id: llmOutput.task_id || task.task_id,
        information_need: llmOutput.information_need || task.information_need,
      });

      outputs.push(ensureCitationCoverage(normalized, high_relevance_results));
    } catch {
      outputs.push(fallbackEvidence(task));
    }
  }

  return outputs;
}
