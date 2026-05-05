export interface PageFetchResult {
  url: string;
  title: string;
  description: string;
  content_excerpt: string;
  text_length: number;
  fetched_at: string;
  error?: string;
}

export interface PageFetchOptions {
  timeout_ms?: number;
  max_chars?: number;
}

const DEFAULT_TIMEOUT_MS = 12000;
const DEFAULT_MAX_CHARS = 2400;

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

function stripHtml(html: string): string {
  const withoutScripts = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
    .replace(/<!--([\s\S]*?)-->/g, " ");

  return decodeEntities(withoutScripts.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
}

function findTagText(html: string, tag: string): string {
  const regex = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, "i");
  const match = html.match(regex);
  if (!match) {
    return "";
  }
  return stripHtml(match[1]);
}

function findMetaContent(html: string, name: string): string {
  const regex = new RegExp(
    `<meta[^>]+(?:name|property)=["']${name}["'][^>]+content=["']([^"']+)["'][^>]*>`,
    "i",
  );
  const match = html.match(regex);
  return match ? decodeEntities(match[1].trim()) : "";
}

function truncate(text: string, maxChars: number): string {
  if (text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, maxChars - 3)}...`;
}

export class PageFetchTool {
  private readonly cache = new Map<string, PageFetchResult>();

  async fetchPage(url: string, options?: PageFetchOptions): Promise<PageFetchResult> {
    const normalizedUrl = url.trim();
    if (!normalizedUrl) {
      return {
        url,
        title: "",
        description: "",
        content_excerpt: "",
        text_length: 0,
        fetched_at: new Date().toISOString(),
        error: "Empty URL",
      };
    }

    const cached = this.cache.get(normalizedUrl);
    if (cached) {
      return cached;
    }

    const timeoutMs = options?.timeout_ms ?? DEFAULT_TIMEOUT_MS;
    const maxChars = options?.max_chars ?? DEFAULT_MAX_CHARS;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);

    try {
      const response = await fetch(normalizedUrl, {
        method: "GET",
        signal: controller.signal,
        headers: {
          "User-Agent": "TouristAgentPrecisionSearch/1.0",
          Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const html = await response.text();
      const title = findTagText(html, "title") || findMetaContent(html, "og:title");
      const description =
        findMetaContent(html, "description") || findMetaContent(html, "og:description");
      const text = stripHtml(html);
      const excerpt = truncate(text, maxChars);

      const result: PageFetchResult = {
        url: normalizedUrl,
        title,
        description,
        content_excerpt: excerpt,
        text_length: text.length,
        fetched_at: new Date().toISOString(),
      };

      this.cache.set(normalizedUrl, result);
      return result;
    } catch (error) {
      const result: PageFetchResult = {
        url: normalizedUrl,
        title: "",
        description: "",
        content_excerpt: "",
        text_length: 0,
        fetched_at: new Date().toISOString(),
        error: error instanceof Error ? error.message : String(error),
      };
      this.cache.set(normalizedUrl, result);
      return result;
    } finally {
      clearTimeout(timer);
    }
  }
}
