import type { SearchQuery } from "../../schemas/searchQuery.schema";
import {
  extractDomain,
  normalizeSearchResult,
  type SearchResult,
} from "../../schemas/searchResult.schema";
import type { SearchTask } from "../../schemas/searchTask.schema";
import { PageFetchTool } from "./pageFetchTool";
import {
  createDefaultSearchProvider,
  type ProviderSearchHit,
  type SearchProvider,
} from "./searchProvider";

export interface WebSearchToolOptions {
  per_query_limit?: number;
  max_pages_fetch?: number;
  max_results_per_task?: number;
}

function canonicalizeUrl(url: string): string {
  try {
    const parsed = new URL(url);
    parsed.hash = "";
    const trackingParams = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"];
    for (const key of trackingParams) {
      parsed.searchParams.delete(key);
    }
    return parsed.toString();
  } catch {
    return url.trim();
  }
}

function normalizeTextFingerprint(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180);
}

function mergeSameDomainSimilarContent(results: SearchResult[]): SearchResult[] {
  const grouped = new Map<string, SearchResult[]>();

  for (const result of results) {
    const domain = result.domain || extractDomain(result.url) || "unknown";
    const fingerprint = normalizeTextFingerprint(`${result.title} ${result.content_excerpt || result.snippet}`);
    const key = `${domain}::${fingerprint}`;
    const list = grouped.get(key) ?? [];
    list.push(result);
    grouped.set(key, list);
  }

  const merged: SearchResult[] = [];
  for (const group of grouped.values()) {
    if (group.length === 1) {
      merged.push(group[0]);
      continue;
    }

    const primary = [...group].sort((a, b) => {
      const aScore = (a.content_excerpt.length || 0) + (a.snippet.length || 0);
      const bScore = (b.content_excerpt.length || 0) + (b.snippet.length || 0);
      return bScore - aScore;
    })[0];

    const mergedUrls = Array.from(new Set(group.map((item) => item.url)));
    merged.push({
      ...primary,
      merged_urls: mergedUrls,
    });
  }

  return merged;
}

function dedupeByUrl(results: SearchResult[]): SearchResult[] {
  const seen = new Set<string>();
  const output: SearchResult[] = [];
  for (const result of results) {
    const key = canonicalizeUrl(result.url).toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    output.push(result);
  }
  return output;
}

async function toSearchResult(
  task: SearchTask,
  query: SearchQuery,
  hit: ProviderSearchHit,
  fetchTool: PageFetchTool,
): Promise<SearchResult> {
  const canonicalUrl = canonicalizeUrl(hit.url);
  const page = await fetchTool.fetchPage(canonicalUrl);

  return normalizeSearchResult({
    task_id: task.task_id,
    query: query.query,
    title: page.title || hit.title,
    url: canonicalUrl,
    domain: extractDomain(canonicalUrl),
    snippet: hit.snippet,
    content_excerpt: page.content_excerpt || hit.snippet,
    source: hit.source,
    published_at: hit.published_at,
  });
}

export class WebSearchTool {
  constructor(
    private readonly provider: SearchProvider = createDefaultSearchProvider(),
    private readonly pageFetchTool: PageFetchTool = new PageFetchTool(),
  ) {}

  async runTaskSearch(
    task: SearchTask,
    queries: SearchQuery[],
    options?: WebSearchToolOptions,
  ): Promise<SearchResult[]> {
    const perQueryLimit = Math.max(1, Math.min(20, options?.per_query_limit ?? 8));
    const maxFetch = Math.max(1, Math.min(80, options?.max_pages_fetch ?? 30));
    const maxPerTask = Math.max(1, Math.min(120, options?.max_results_per_task ?? 40));

    const collected: SearchResult[] = [];

    for (const query of queries) {
      const hits = await this.provider.search(query.query, {
        limit: perQueryLimit,
        freshness_required: query.freshness_required || task.freshness_required,
      });

      for (const hit of hits) {
        if (collected.length >= maxFetch) {
          break;
        }
        const row = await toSearchResult(task, query, hit, this.pageFetchTool);
        collected.push(row);
      }
      if (collected.length >= maxFetch) {
        break;
      }
    }

    const deduped = dedupeByUrl(collected);
    const merged = mergeSameDomainSimilarContent(deduped);

    // Keep domain diversity first, then add remaining high-content rows.
    const byDomain = new Map<string, SearchResult[]>();
    for (const row of merged) {
      const domain = row.domain || "unknown";
      const list = byDomain.get(domain) ?? [];
      list.push(row);
      byDomain.set(domain, list);
    }

    const diversified: SearchResult[] = [];
    for (const rows of byDomain.values()) {
      rows.sort((a, b) => b.content_excerpt.length - a.content_excerpt.length);
      diversified.push(rows[0]);
    }

    const remaining = merged
      .filter((row) => !diversified.some((base) => base.url === row.url))
      .sort((a, b) => b.content_excerpt.length - a.content_excerpt.length);

    const finalList = [...diversified, ...remaining].slice(0, maxPerTask);
    return finalList;
  }
}
