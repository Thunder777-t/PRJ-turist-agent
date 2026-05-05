import type { FreshnessRequired } from "../../schemas/searchTask.schema";

export interface ProviderSearchHit {
  title: string;
  url: string;
  snippet: string;
  source: string;
  published_at?: string;
}

export interface SearchProviderOptions {
  limit?: number;
  freshness_required?: FreshnessRequired;
  language?: string;
}

export interface SearchProvider {
  search(query: string, options?: SearchProviderOptions): Promise<ProviderSearchHit[]>;
}

const DEFAULT_TIMEOUT_MS = 12000;
const DEFAULT_LIMIT = 8;

function decodeEntities(input: string): string {
  return input
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x2F;/g, "/")
    .replace(/&#x27;/g, "'");
}

function stripHtml(input: string): string {
  return decodeEntities(input.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim());
}

function getTagValue(xml: string, tagName: string): string {
  const regex = new RegExp(`<${tagName}>([\\s\\S]*?)<\\/${tagName}>`, "i");
  const match = xml.match(regex);
  return match ? stripHtml(match[1]) : "";
}

function parseRssItems(xml: string, source: string): ProviderSearchHit[] {
  const items: ProviderSearchHit[] = [];
  const itemMatches = xml.match(/<item>[\s\S]*?<\/item>/gi) ?? [];
  for (const itemXml of itemMatches) {
    const title = getTagValue(itemXml, "title");
    const url = getTagValue(itemXml, "link");
    const snippet = getTagValue(itemXml, "description");
    const published_at = getTagValue(itemXml, "pubDate") || undefined;
    if (!url || !title) {
      continue;
    }
    items.push({ title, url, snippet, source, published_at });
  }
  return items;
}

async function fetchText(url: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "GET",
      signal: controller.signal,
      headers: {
        "User-Agent": "TouristAgentPrecisionSearch/1.0",
        Accept: "application/xml,text/xml,text/plain,*/*",
      },
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.text();
  } finally {
    clearTimeout(timer);
  }
}

export class BingRssSearchProvider implements SearchProvider {
  async search(query: string, options?: SearchProviderOptions): Promise<ProviderSearchHit[]> {
    const limit = Math.max(1, Math.min(20, options?.limit ?? DEFAULT_LIMIT));
    const freshness = options?.freshness_required ?? "medium";

    const freshnessHint = freshness === "high" ? " 最新 官方" : "";
    const language = options?.language?.trim() || "zh-CN";
    const url = new URL("https://www.bing.com/search");
    url.searchParams.set("q", `${query}${freshnessHint}`.trim());
    url.searchParams.set("format", "rss");
    url.searchParams.set("setlang", language);

    const xml = await fetchText(url.toString());
    const parsed = parseRssItems(xml, "bing_rss");

    const deduped: ProviderSearchHit[] = [];
    const seen = new Set<string>();
    for (const item of parsed) {
      const key = item.url.trim().toLowerCase();
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      deduped.push(item);
      if (deduped.length >= limit) {
        break;
      }
    }
    return deduped;
  }
}

export class SearchProviderWithFallback implements SearchProvider {
  constructor(private readonly providers: SearchProvider[]) {}

  async search(query: string, options?: SearchProviderOptions): Promise<ProviderSearchHit[]> {
    const errors: string[] = [];
    for (const provider of this.providers) {
      try {
        const result = await provider.search(query, options);
        if (result.length > 0) {
          return result;
        }
      } catch (error) {
        errors.push(error instanceof Error ? error.message : String(error));
      }
    }
    if (errors.length > 0) {
      throw new Error(`All providers failed: ${errors.join(" | ")}`);
    }
    return [];
  }
}

export function createDefaultSearchProvider(): SearchProvider {
  return new SearchProviderWithFallback([new BingRssSearchProvider()]);
}
