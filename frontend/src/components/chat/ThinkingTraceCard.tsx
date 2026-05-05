import { useEffect, useMemo, useState } from "react";

import type { ThinkingTrace } from "./thinkingTypes";

type ThinkingTraceCardProps = {
  trace: ThinkingTrace;
  language: "zh" | "en";
};

function trimText(value: string, maxLength = 420): string {
  const text = (value || "").trim();
  if (!text) {
    return "";
  }
  return text.length <= maxLength ? text : `${text.slice(0, maxLength)}...`;
}

function getDurationSeconds(trace: ThinkingTrace, nowMs: number): number {
  const end = trace.endedAtMs ?? nowMs;
  const diff = Math.max(0, end - trace.startedAtMs);
  return Math.max(1, Math.round(diff / 1000));
}

export default function ThinkingTraceCard({ trace, language }: ThinkingTraceCardProps) {
  const [expanded, setExpanded] = useState(trace.status === "running");
  const [showAllSources, setShowAllSources] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    setNowMs(Date.now());
    if (trace.status !== "running") {
      return;
    }
    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [trace.status, trace.startedAtMs, trace.endedAtMs]);

  useEffect(() => {
    if (trace.status === "running") {
      setExpanded(true);
    }
  }, [trace.status]);

  const durationSeconds = useMemo(() => getDurationSeconds(trace, nowMs), [nowMs, trace]);
  const sourcesToShow = showAllSources ? trace.sources : trace.sources.slice(0, 6);
  const understandingText = trimText(trace.understanding || trace.plannerSummary);
  const finalText = trimText(trace.finalSummary);
  const updates = Array.isArray(trace.liveUpdates) ? trace.liveUpdates : [];
  const summaryLabel = language === "zh" ? "思考过程" : "Thought process";
  const durationLabel = language === "zh" ? `用时 ${durationSeconds} 秒` : `${durationSeconds}s`;
  const searchedLabel = language === "zh" ? "搜索到" : "Searched";
  const pagesLabel = language === "zh" ? "个网页" : "pages";
  const browsingLabel = language === "zh" ? "浏览" : "Reviewed";
  const showAllLabel = language === "zh" ? "查看全部" : "View all";
  const showLessLabel = language === "zh" ? "收起列表" : "Show less";
  const fallbackSummary =
    language === "zh"
      ? "已完成需求理解、搜索和整合，正在准备最终回答。"
      : "Need understanding, search, and synthesis completed. Preparing final response.";

  return (
    <section className={`thinking-trace-card ${expanded ? "expanded" : "collapsed"}`}>
      <button
        type="button"
        className="thinking-trace-toggle"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
      >
        <span className="thinking-trace-icon" aria-hidden="true">
          ✦
        </span>
        <span className="thinking-trace-title">
          {summaryLabel}
          <span className="thinking-trace-duration">（{durationLabel}）</span>
        </span>
        <span className="thinking-trace-chevron" aria-hidden="true">
          {expanded ? "▼" : "▶"}
        </span>
      </button>

      {expanded ? (
        <div className="thinking-trace-body">
          {updates.length ? (
            updates.map((update) => (
              <div key={update.id} className="thinking-item">
                <div className="thinking-bullet" aria-hidden="true" />
                <div className="thinking-text">{trimText(update.text, 300)}</div>
              </div>
            ))
          ) : understandingText ? (
            <div className="thinking-item">
              <div className="thinking-bullet" aria-hidden="true" />
              <div className="thinking-text">{understandingText}</div>
            </div>
          ) : null}

          {trace.searchRounds.map((round) => (
            <div key={`search-round-${round.round}`} className="thinking-item">
              <div className="thinking-search-title">
                {searchedLabel} {round.candidateCount} {pagesLabel}
              </div>
              <div className="thinking-text">
                {language === "zh"
                  ? `第 ${round.round} 轮筛选后保留 ${round.highRelevanceCount} 条高相关结果${
                      round.insufficientTaskCount > 0 ? `，仍有 ${round.insufficientTaskCount} 个任务信息不足` : ""
                    }。`
                  : `Round ${round.round} kept ${round.highRelevanceCount} high-relevance results${
                      round.insufficientTaskCount > 0
                        ? `, with ${round.insufficientTaskCount} tasks still lacking evidence`
                        : ""
                    }.`}
              </div>
            </div>
          ))}

          {trace.searchQueries.length ? (
            <div className="thinking-item">
              <div className="thinking-subtitle">{language === "zh" ? "搜索 Query" : "Search queries"}</div>
              <div className="thinking-query-list">
                {trace.searchQueries.map((query) => (
                  <span key={query} className="thinking-query-chip">
                    {query}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {trace.sources.length ? (
            <div className="thinking-item">
              <div className="thinking-search-title">
                {browsingLabel} {trace.sources.length} {language === "zh" ? "个页面" : "pages"}
              </div>
              <div className="thinking-source-list">
                {sourcesToShow.map((source) => (
                  <a key={`${source.url}-${source.title}`} href={source.url} target="_blank" rel="noreferrer">
                    {source.title}
                  </a>
                ))}
              </div>
              {trace.sources.length > 6 ? (
                <button
                  type="button"
                  className="thinking-source-toggle"
                  onClick={() => setShowAllSources((prev) => !prev)}
                >
                  {showAllSources ? showLessLabel : showAllLabel}
                </button>
              ) : null}
            </div>
          ) : null}

          <div className="thinking-item">
            <div className="thinking-bullet" aria-hidden="true" />
            <div className="thinking-text">{finalText || fallbackSummary}</div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
