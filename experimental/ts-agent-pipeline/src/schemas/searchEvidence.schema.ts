export interface EvidenceCitation {
  title: string;
  url: string;
  domain: string;
  relevance_score: number;
}

export interface SearchEvidence {
  task_id: string;
  information_need: string;
  evidence_summary: string;
  citations: EvidenceCitation[];
  confidence: number;
  unresolved_gaps: string[];
  is_information_insufficient: boolean;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.trim() : fallback;
}

function asScore(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, Math.min(100, Math.round(value)));
  }
  return 0;
}

function parseCitation(payload: unknown): EvidenceCitation | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const row = payload as Partial<EvidenceCitation>;
  const url = asString(row.url);
  if (!url) {
    return null;
  }
  return {
    title: asString(row.title, url),
    url,
    domain: asString(row.domain),
    relevance_score: asScore(row.relevance_score),
  };
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter((item) => item.length > 0);
}

export function normalizeSearchEvidence(input: Partial<SearchEvidence>): SearchEvidence {
  return {
    task_id: asString(input.task_id),
    information_need: asString(input.information_need),
    evidence_summary: asString(input.evidence_summary),
    citations: Array.isArray(input.citations)
      ? input.citations
          .map((item) => parseCitation(item))
          .filter((item): item is EvidenceCitation => Boolean(item))
      : [],
    confidence: asScore(input.confidence),
    unresolved_gaps: asStringArray(input.unresolved_gaps),
    is_information_insufficient: Boolean(input.is_information_insufficient),
  };
}

export function parseSearchEvidenceList(payload: unknown): SearchEvidence[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const raw = (payload as { evidences?: unknown }).evidences;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.map((item) => normalizeSearchEvidence(item as Partial<SearchEvidence>));
}
