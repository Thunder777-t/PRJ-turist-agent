import type { TraceLink } from "../../types";

export type ThinkingTraceStatus = "running" | "done" | "error" | "stopped";

export type ThinkingSearchRound = {
  round: number;
  candidateCount: number;
  highRelevanceCount: number;
  insufficientTaskCount: number;
};

export type ThinkingTraceUpdate = {
  id: string;
  text: string;
  atMs: number;
};

export type ThinkingTrace = {
  startedAtMs: number;
  endedAtMs: number | null;
  status: ThinkingTraceStatus;
  understanding: string;
  plannerSummary: string;
  finalSummary: string;
  liveUpdates: ThinkingTraceUpdate[];
  searchQueries: string[];
  searchRounds: ThinkingSearchRound[];
  sources: TraceLink[];
};
