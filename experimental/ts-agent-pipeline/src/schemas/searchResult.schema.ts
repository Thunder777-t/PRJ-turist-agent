export interface SearchResult {
  task_id: string;
  query: string;
  title: string;
  url: string;
  domain: string;
  snippet: string;
  content_excerpt: string;
  source: string;
  published_at?: string;
  language?: string;
  merged_urls?: string[];
}

export interface SearchResultsByTask {
  task_id: string;
  results: SearchResult[];
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.trim() : fallback;
}

export function extractDomain(url: string): string {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.startsWith("www.") ? host.slice(4) : host;
  } catch {
    return "";
  }
}

export function normalizeSearchResult(input: Partial<SearchResult>): SearchResult {
  const url = asString(input.url);
  return {
    task_id: asString(input.task_id),
    query: asString(input.query),
    title: asString(input.title, url || "Untitled"),
    url,
    domain: asString(input.domain, extractDomain(url)),
    snippet: asString(input.snippet),
    content_excerpt: asString(input.content_excerpt),
    source: asString(input.source, "web"),
    published_at: asString(input.published_at) || undefined,
    language: asString(input.language) || undefined,
    merged_urls: Array.isArray(input.merged_urls)
      ? input.merged_urls
          .map((item) => asString(item))
          .filter((item) => item.length > 0)
      : undefined,
  };
}

export function normalizeSearchResults(payload: unknown): SearchResult[] {
  if (!Array.isArray(payload)) {
    return [];
  }
  return payload
    .map((result) => normalizeSearchResult(result as Partial<SearchResult>))
    .filter((result) => result.url.length > 0 && result.title.length > 0);
}
