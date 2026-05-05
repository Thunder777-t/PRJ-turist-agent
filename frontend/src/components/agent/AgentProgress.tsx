import { useEffect, useMemo, useState } from "react";

export type AgentStepStatus = "pending" | "running" | "done" | "error";

export type AgentProgressStep = {
  id: string;
  title: string;
  description: string;
  status: AgentStepStatus;
  queries?: string[];
  filteredCount?: number | null;
  errorMessage?: string;
};

type AgentProgressProps = {
  steps: AgentProgressStep[];
  running: boolean;
  onRetry?: () => void;
  labels: {
    ariaLabel: string;
    statusPending: string;
    statusRunning: string;
    statusDone: string;
    statusError: string;
    expand: string;
    collapse: string;
    retry: string;
    step: string;
    searchQuery: string;
    filteredResults: (count: number) => string;
  };
};

function statusLabel(status: AgentStepStatus, labels: AgentProgressProps["labels"]): string {
  if (status === "running") {
    return labels.statusRunning;
  }
  if (status === "done") {
    return labels.statusDone;
  }
  if (status === "error") {
    return labels.statusError;
  }
  return labels.statusPending;
}

export default function AgentProgress({ steps, running, onRetry, labels }: AgentProgressProps) {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (running) {
      setExpanded(false);
    }
  }, [running]);

  const currentStep = useMemo(() => {
    const executing = steps.find((step) => step.status === "running");
    if (executing) {
      return executing;
    }
    const errored = steps.find((step) => step.status === "error");
    if (errored) {
      return errored;
    }
    const done = [...steps].reverse().find((step) => step.status === "done");
    return done ?? steps[0] ?? null;
  }, [steps]);

  if (!currentStep) {
    return null;
  }

  const hasError = steps.some((step) => step.status === "error");

  return (
    <section className="agent-progress" aria-label={labels.ariaLabel}>
      <div className="agent-progress-current">
        <span className={`step-dot ${currentStep.status}`} aria-hidden="true" />
        <div className="agent-progress-current-main">
          <div className="agent-progress-current-title">{currentStep.title}</div>
          <div className="agent-progress-current-desc">{currentStep.description}</div>
        </div>
        <div className={`step-badge ${currentStep.status}`}>{statusLabel(currentStep.status, labels)}</div>
      </div>

      <div className="agent-progress-actions">
        <button type="button" className="agent-link-btn" onClick={() => setExpanded((prev) => !prev)}>
          {expanded ? labels.collapse : labels.expand}
        </button>
        {hasError && onRetry ? (
          <button type="button" className="agent-link-btn danger" onClick={onRetry}>
            {labels.retry}
          </button>
        ) : null}
      </div>

      {expanded ? (
        <div className="agent-step-list">
          {steps.map((step, idx) => (
            <article key={step.id} className={`agent-step-card ${step.status}`}>
              <div className="agent-step-top">
                <div className="agent-step-left">
                  <span className={`step-dot ${step.status}`} aria-hidden="true" />
                  <span className="agent-step-index">{labels.step} {idx + 1}</span>
                </div>
                <span className={`step-badge ${step.status}`}>{statusLabel(step.status, labels)}</span>
              </div>

              <div className="agent-step-title">{step.title}</div>
              <div className="agent-step-desc">{step.description}</div>

              {step.queries && step.queries.length ? (
                <div className="agent-step-meta">
                  <div className="agent-step-meta-title">{labels.searchQuery}</div>
                  <ul className="agent-query-list">
                    {step.queries.map((query) => (
                      <li key={`${step.id}-${query}`}>{query}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {typeof step.filteredCount === "number" ? (
                <div className="agent-step-meta">
                  {labels.filteredResults(step.filteredCount)}
                </div>
              ) : null}

              {step.status === "error" && step.errorMessage ? (
                <div className="agent-step-error">{step.errorMessage}</div>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
