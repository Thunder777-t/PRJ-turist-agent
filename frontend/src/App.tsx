import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  clearStoredAuth,
  createConversation,
  deleteConversation,
  getMe,
  getStoredAuth,
  listConversations,
  listMessages,
  login,
  logout,
  patchConversation,
  register,
  setStoredAuth,
  streamMessage,
} from "./lib/api";
import type { Conversation, Message, StreamEvent, Tokens, TraceLink, User } from "./types";
import type { AgentProgressStep, AgentStepStatus } from "./components/agent/AgentProgress";
import ChatMessageBubble from "./components/chat/ChatMessageBubble";
import ChatEmptyState from "./components/chat/ChatEmptyState";
import RecentTravelPlans from "./components/layout/RecentTravelPlans";
import { parseTravelPlanFromPayload } from "./components/travel-plan/normalizeTravelPlan";
import type { TravelPlanPayload } from "./components/travel-plan/types";
import type {
  ThinkingSearchRound,
  ThinkingTrace,
} from "./components/chat/thinkingTypes";

type AgentStage =
  | "idle"
  | "understanding"
  | "planning"
  | "searching"
  | "synthesizing"
  | "drafting"
  | "completed"
  | "stopped";

type UiLanguage = "zh" | "en";

type AgentStepMeta = {
  id: string;
  title: string;
  description: string;
};

type RequirementSummaryLabels = {
  goalLabel: string;
  detectedLabel: string;
  pendingLabel: string;
  blockingTag: string;
  draftAvailable: string;
  needMoreInfo: string;
};

type UiText = {
  appName: string;
  loading: string;
  auth: {
    title: string;
    subtitle: string;
    login: string;
    register: string;
    email: string;
    username: string;
    password: string;
    processing: string;
    createAccount: string;
    restoreFailed: string;
    authFailed: string;
  };
  sidebar: {
    newTrip: string;
    newChatTitle: string;
    rename: string;
    delete: string;
    confirm: string;
    cancel: string;
    save: string;
    newTitlePlaceholder: string;
    noConversation: string;
    recentTitle: string;
    recentEmpty: string;
    logout: string;
  };
  header: {
    agentName: string;
    toggleSidebar: string;
    languageSwitcher: string;
  };
  emptyState: {
    title: string;
    subtitle: string;
    ariaLabel: string;
    prompts: string[];
  };
  quickPrompts: string[];
  runMode: {
    ariaLabel: string;
    quality: string;
    fast: string;
  };
  composer: {
    placeholder: string;
    send: string;
    stop: string;
  };
  messageBubble: {
    userRole: string;
    assistantRole: string;
    thinking: string;
  };
  agent: {
    processingDescription: string;
    defaultSteps: AgentStepMeta[];
    stageLabels: Record<AgentStage, string>;
    runningFor: (seconds: number) => string;
    afterDoneHint: string;
    reviewingHint: string;
    startUnderstanding: string;
    messageStartFallback: string;
    plannerFallback: string;
    searchDoneFallback: string;
    finalAnalyzedFallback: string;
    flowErrorFallback: string;
    flowErrorSummaryFallback: string;
    summaryGenerating: string;
    summaryWaiting: string;
    noSources: string;
    traceTitles: {
      understanding: string;
      summary: string;
      sources: string;
    };
    userStoppedUnderstanding: string;
    userStoppedSummary: string;
    manualStoppedNote: string;
    toolFailed: string;
    processFailed: string;
    requirementLabels: RequirementSummaryLabels;
    progressLabels: {
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
  errors: {
    loadConversations: string;
    loadMessages: string;
    createConversation: string;
    renameConversation: string;
    deleteConversation: string;
    sendMessage: string;
    missingConversationId: string;
  };
};

const UI_LANGUAGE_KEY = "tourist_agent_ui_lang";
const THINKING_TRACE_STORAGE_KEY = "tourist_agent_thinking_trace_v1";
const MESSAGE_LANGUAGE_STORAGE_KEY = "tourist_agent_message_lang_v1";

const UI_TEXT: Record<UiLanguage, UiText> = {
  zh: {
    appName: "Tourist Agent",
    loading: "正在加载 Tourist Agent...",
    auth: {
      title: "Tourist Agent",
      subtitle: "和智能旅行助理一起规划下一段旅程。",
      login: "登录",
      register: "注册",
      email: "邮箱",
      username: "用户名",
      password: "密码",
      processing: "处理中...",
      createAccount: "创建账号",
      restoreFailed: "会话恢复失败，请重新登录。",
      authFailed: "认证失败，请重试。",
    },
    sidebar: {
      newTrip: "+ 新建旅行",
      newChatTitle: "新旅行对话",
      rename: "重命名",
      delete: "删除",
      confirm: "确认",
      cancel: "取消",
      save: "保存",
      newTitlePlaceholder: "新标题",
      noConversation: "还没有对话。",
      recentTitle: "最近旅行计划",
      recentEmpty: "暂无最近计划。",
      logout: "退出登录",
    },
    header: {
      agentName: "AI Travel Planner",
      toggleSidebar: "切换会话侧栏",
      languageSwitcher: "语言切换",
    },
    emptyState: {
      title: "今天想去哪儿？",
      subtitle: "告诉我目的地、天数、预算或旅行偏好，我会帮你搜索、筛选并生成行程。",
      ariaLabel: "对话空状态",
      prompts: [
        "我想去成都旅游7天，预算8000",
        "下个月带爸妈去云南，不想太累",
        "我一个人去日本关西玩5天",
        "想找一个适合美食和慢生活的城市",
        "帮我规划一个情侣三亚4天行程",
        "我想周末从上海出发短途旅行",
      ],
    },
    quickPrompts: [
      "帮我做一个5天轻松行程",
      "先给我预算分档方案",
      "优先安排地道美食",
      "按雨天备选重排路线",
    ],
    runMode: {
      ariaLabel: "运行模式",
      quality: "质量模式",
      fast: "极速模式",
    },
    composer: {
      placeholder: "告诉我目的地、出发时间、预算和偏好...",
      send: "发送",
      stop: "停止",
    },
    messageBubble: {
      userRole: "你",
      assistantRole: "AI Travel Planner",
      thinking: "思考中...",
    },
    agent: {
      processingDescription: "处理中...",
      defaultSteps: [
        {
          id: "understand",
          title: "正在理解你的需求",
          description: "分析目的地、天数、预算和偏好，识别缺失信息。",
        },
        {
          id: "plan",
          title: "正在拆解搜索任务",
          description: "将需求转成可执行的信息检索任务。",
        },
        {
          id: "search",
          title: "正在联网搜索",
          description: "执行多条查询并收集候选来源。",
        },
        {
          id: "filter",
          title: "正在筛选高相关网页",
          description: "去重并保留高相关证据，剔除低价值结果。",
        },
        {
          id: "integrate",
          title: "正在整合旅行方案",
          description: "整合证据并生成可执行行程建议。",
        },
      ],
      stageLabels: {
        idle: "等待开始",
        understanding: "正在理解你的真实需求",
        planning: "正在规划搜索任务",
        searching: "正在联网检索高相关信息",
        synthesizing: "正在筛选证据并归纳",
        drafting: "正在生成最终方案",
        completed: "本次生成已完成",
        stopped: "你已手动停止本次生成",
      },
      runningFor: (seconds) => `已运行 ${seconds}s`,
      afterDoneHint: "你可以继续提问，或修改后重发。",
      reviewingHint: "你正在查看历史消息，已暂停自动滚动到底部。",
      startUnderstanding: "正在理解你的问题...",
      messageStartFallback: "已分析用户需求，正在检索相关信息。",
      plannerFallback: "已完成需求理解。",
      searchDoneFallback: "已完成检索。",
      finalAnalyzedFallback: "已完成分析。",
      flowErrorFallback: "流程发生异常。",
      flowErrorSummaryFallback: "执行中出现异常，已触发回退。",
      summaryGenerating: "正在生成总结...",
      summaryWaiting: "等待总结结果。",
      noSources: "暂无可展示来源。",
      traceTitles: {
        understanding: "问题理解",
        summary: "总结归纳",
        sources: "检索网页",
      },
      userStoppedUnderstanding: "你已停止本次生成。",
      userStoppedSummary: "输入内容已保留，你可以直接修改后重发。",
      manualStoppedNote: "[已手动停止]",
      toolFailed: "工具调用失败",
      processFailed: "流程执行失败",
      requirementLabels: {
        goalLabel: "目标理解",
        detectedLabel: "已识别信息",
        pendingLabel: "待确认",
        blockingTag: "关键",
        draftAvailable: "可先输出初版方案，并继续迭代。",
        needMoreInfo: "信息尚不完整，建议先补充关键条件。",
      },
      progressLabels: {
        ariaLabel: "Agent 过程",
        statusPending: "待执行",
        statusRunning: "进行中",
        statusDone: "已完成",
        statusError: "失败",
        expand: "展开全部步骤",
        collapse: "收起步骤",
        retry: "重试",
        step: "步骤",
        searchQuery: "搜索查询",
        filteredResults: (count) => `已筛选 ${count} 条高相关结果`,
      },
    },
    errors: {
      loadConversations: "加载会话失败。",
      loadMessages: "加载消息失败。",
      createConversation: "创建会话失败。",
      renameConversation: "重命名会话失败。",
      deleteConversation: "删除会话失败。",
      sendMessage: "发送消息失败。",
      missingConversationId: "缺少会话 ID。",
    },
  },
  en: {
    appName: "Tourist Agent",
    loading: "Loading Tourist Agent...",
    auth: {
      title: "Tourist Agent",
      subtitle: "Plan your next journey with a live travel assistant.",
      login: "Login",
      register: "Register",
      email: "Email",
      username: "Username",
      password: "Password",
      processing: "Processing...",
      createAccount: "Create account",
      restoreFailed: "Failed to restore session. Please login again.",
      authFailed: "Authentication failed. Please retry.",
    },
    sidebar: {
      newTrip: "+ New Trip",
      newChatTitle: "New travel chat",
      rename: "Rename",
      delete: "Delete",
      confirm: "Confirm",
      cancel: "Cancel",
      save: "Save",
      newTitlePlaceholder: "New title",
      noConversation: "No conversation yet.",
      recentTitle: "Recent Travel Plans",
      recentEmpty: "No recent plans.",
      logout: "Logout",
    },
    header: {
      agentName: "AI Travel Planner",
      toggleSidebar: "Toggle conversation sidebar",
      languageSwitcher: "Language switcher",
    },
    emptyState: {
      title: "Where do you want to go today?",
      subtitle: "Tell me your destination, days, budget, or travel style. I will search, filter, and build your itinerary.",
      ariaLabel: "Empty conversation",
      prompts: [
        "I want a 7-day Chengdu trip with an 8,000 RMB budget",
        "Going to Yunnan with my parents next month, keep it relaxed",
        "I am traveling solo to Kansai for 5 days",
        "Recommend a city for food and slow travel",
        "Plan a 4-day Sanya couple trip",
        "I want a weekend short trip from Shanghai",
      ],
    },
    quickPrompts: [
      "Build a relaxed 5-day itinerary",
      "Show budget tiers first",
      "Prioritize authentic local food",
      "Replan with rainy-day alternatives",
    ],
    runMode: {
      ariaLabel: "Run mode",
      quality: "Quality mode",
      fast: "Fast mode",
    },
    composer: {
      placeholder: "Tell me your destination, dates, budget, and preferences...",
      send: "Send",
      stop: "Stop",
    },
    messageBubble: {
      userRole: "You",
      assistantRole: "AI Travel Planner",
      thinking: "Thinking...",
    },
    agent: {
      processingDescription: "Processing...",
      defaultSteps: [
        {
          id: "understand",
          title: "Understanding your needs",
          description: "Extracting destination, days, budget, and preferences; spotting missing info.",
        },
        {
          id: "plan",
          title: "Planning search tasks",
          description: "Turning your needs into executable search tasks.",
        },
        {
          id: "search",
          title: "Searching the web",
          description: "Running multiple queries and collecting candidate sources.",
        },
        {
          id: "filter",
          title: "Filtering high-relevance pages",
          description: "Deduplicating and keeping high-value evidence only.",
        },
        {
          id: "integrate",
          title: "Composing your plan",
          description: "Synthesizing evidence into an actionable itinerary.",
        },
      ],
      stageLabels: {
        idle: "Waiting to start",
        understanding: "Understanding your real travel intent",
        planning: "Planning search tasks",
        searching: "Searching for high-relevance information",
        synthesizing: "Filtering and synthesizing evidence",
        drafting: "Generating the final answer",
        completed: "Generation completed",
        stopped: "Generation stopped by you",
      },
      runningFor: (seconds) => `Running for ${seconds}s`,
      afterDoneHint: "You can continue asking or edit and resend.",
      reviewingHint: "You are reviewing chat history. Auto-scroll is paused.",
      startUnderstanding: "Understanding your request...",
      messageStartFallback: "Need analysis completed. Searching relevant information now.",
      plannerFallback: "Need understanding completed.",
      searchDoneFallback: "Search completed.",
      finalAnalyzedFallback: "Analysis completed.",
      flowErrorFallback: "The workflow encountered an error.",
      flowErrorSummaryFallback: "An error occurred during execution. Fallback has been applied.",
      summaryGenerating: "Generating summary...",
      summaryWaiting: "Waiting for summary output.",
      noSources: "No sources to display yet.",
      traceTitles: {
        understanding: "Need Understanding",
        summary: "Summary",
        sources: "Retrieved Pages",
      },
      userStoppedUnderstanding: "Generation stopped by you.",
      userStoppedSummary: "Your input is kept. Edit and resend anytime.",
      manualStoppedNote: "[Stopped by user]",
      toolFailed: "Tool call failed",
      processFailed: "Workflow failed",
      requirementLabels: {
        goalLabel: "Goal",
        detectedLabel: "Detected",
        pendingLabel: "Pending",
        blockingTag: "blocking",
        draftAvailable: "A draft plan can be provided now and refined later.",
        needMoreInfo: "Information is still incomplete. Please add key details.",
      },
      progressLabels: {
        ariaLabel: "Agent progress",
        statusPending: "pending",
        statusRunning: "running",
        statusDone: "done",
        statusError: "error",
        expand: "Expand all",
        collapse: "Collapse",
        retry: "Retry",
        step: "Step",
        searchQuery: "Search queries",
        filteredResults: (count) => `${count} high-relevance results kept`,
      },
    },
    errors: {
      loadConversations: "Failed to load conversations.",
      loadMessages: "Failed to load messages.",
      createConversation: "Failed to create conversation.",
      renameConversation: "Failed to rename conversation.",
      deleteConversation: "Failed to delete conversation.",
      sendMessage: "Failed to send message.",
      missingConversationId: "Conversation ID is missing.",
    },
  },
};

const TRACE_EVENT_TEXT = {
  zh: {
    start: "正在理解你的真实需求，并规划检索任务...",
    heartbeat: (seconds: number) => `仍在联网搜索与筛选证据（${seconds}s）...`,
    planner: (taskCount: number, queryCount: number) => `已拆解 ${taskCount} 个搜索任务，准备执行 ${queryCount} 条检索。`,
    source: (count: number) => `已筛选 ${count} 条高相关网页。`,
    composing: "证据整合完成，正在生成最终回答。",
    finalized: "检索与整合已完成，正在输出完整方案。",
    runningQuery: (query: string) => `正在检索：${query}`,
    queryError: (message: string) => `检索异常：${message}`,
    flowError: (message: string) => `流程异常：${message}`,
  },
  en: {
    start: "Understanding your request and planning search tasks...",
    heartbeat: (seconds: number) => `Still searching and filtering evidence (${seconds}s)...`,
    planner: (taskCount: number, queryCount: number) =>
      `Planned ${taskCount} search tasks and prepared ${queryCount} queries.`,
    source: (count: number) => `Kept ${count} high-relevance pages.`,
    composing: "Evidence synthesis completed. Composing final answer.",
    finalized: "Search and synthesis completed. Rendering final itinerary.",
    runningQuery: (query: string) => `Searching: ${query}`,
    queryError: (message: string) => `Search error: ${message}`,
    flowError: (message: string) => `Workflow error: ${message}`,
  },
} as const;

function stageToStepIndex(stage: AgentStage): number {
  if (stage === "understanding") {
    return 0;
  }
  if (stage === "planning") {
    return 1;
  }
  if (stage === "searching") {
    return 2;
  }
  if (stage === "synthesizing") {
    return 3;
  }
  if (stage === "drafting" || stage === "completed") {
    return 4;
  }
  return 0;
}

function formatTime(input: string): string {
  return new Date(input).toLocaleString([], { hour: "2-digit", minute: "2-digit" });
}

function isNearBottom(element: HTMLElement, threshold = 72): boolean {
  const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
  return remaining <= threshold;
}

function buildConversationTitle(input: string, fallback: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    return fallback;
  }
  return trimmed.length <= 30 ? trimmed : `${trimmed.slice(0, 30)}...`;
}

function messageContentFromEvent(event: StreamEvent): string {
  const data = event.data;
  if (typeof data.text === "string") {
    return data.text;
  }
  if (typeof data.response === "string") {
    return data.response;
  }
  if (typeof data.message === "string") {
    return data.message;
  }
  return "";
}

function dedupeTraceSources(items: TraceLink[]): TraceLink[] {
  const rows: TraceLink[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const url = (item.url || "").trim();
    if (!url) {
      continue;
    }
    const key = url.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    rows.push({
      title: (item.title || url).trim() || url,
      url,
      platform: item.platform?.trim() || undefined,
      snippet: item.snippet?.trim() || undefined,
    });
    if (rows.length >= 16) {
      break;
    }
  }
  return rows;
}

function parseSearchRoundsFromDiagnostics(raw: unknown): ThinkingSearchRound[] {
  const root = asRecord(raw);
  const rounds = Array.isArray(root?.rounds) ? root.rounds : [];
  const output: ThinkingSearchRound[] = [];

  for (const row of rounds) {
    const item = asRecord(row);
    if (!item) {
      continue;
    }
    const round = typeof item.round === "number" && Number.isFinite(item.round) ? item.round : output.length + 1;
    const quality = Array.isArray(item.task_quality) ? item.task_quality : [];
    let candidateCount = 0;
    let highRelevanceCount = 0;

    for (const q of quality) {
      const taskRow = asRecord(q);
      if (!taskRow) {
        continue;
      }
      const candidates =
        typeof taskRow.total_candidates === "number" && Number.isFinite(taskRow.total_candidates)
          ? taskRow.total_candidates
          : 0;
      const high =
        typeof taskRow.high_relevance_results === "number" && Number.isFinite(taskRow.high_relevance_results)
          ? taskRow.high_relevance_results
          : 0;
      candidateCount += Math.max(0, candidates);
      highRelevanceCount += Math.max(0, high);
    }

    const insufficientTaskCount = Array.isArray(item.insufficient_task_ids)
      ? item.insufficient_task_ids.filter((id) => typeof id === "string" && id.trim().length > 0).length
      : 0;

    output.push({
      round,
      candidateCount,
      highRelevanceCount,
      insufficientTaskCount,
    });
  }

  return output.slice(0, 3);
}

function mergeUniqueQueries(base: string[], additions: string[]): string[] {
  const seen = new Set(base.map((item) => item.toLowerCase()));
  const rows = [...base];
  for (const query of additions) {
    const normalized = query.trim();
    if (!normalized) {
      continue;
    }
    const key = normalized.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    rows.push(normalized);
    if (rows.length >= 10) {
      break;
    }
  }
  return rows;
}

function detectMessageLanguage(text: string, fallback: UiLanguage = "en"): UiLanguage {
  const raw = String(text || "").trim();
  if (!raw) {
    return fallback;
  }
  const cjkCount = (raw.match(/[\u4e00-\u9fff]/g) || []).length;
  const latinCount = (raw.match(/[A-Za-z]/g) || []).length;
  if (cjkCount > 0 && latinCount > 0) {
    return latinCount >= cjkCount * 2 ? "en" : "zh";
  }
  if (cjkCount > 0) {
    return "zh";
  }
  if (latinCount > 0) {
    return "en";
  }
  return fallback;
}

function readThinkingTraceStore(): Record<string, Record<string, ThinkingTrace>> {
  const raw = localStorage.getItem(THINKING_TRACE_STORAGE_KEY);
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw) as Record<string, Record<string, ThinkingTrace>>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeThinkingTraceStore(store: Record<string, Record<string, ThinkingTrace>>): void {
  localStorage.setItem(THINKING_TRACE_STORAGE_KEY, JSON.stringify(store));
}

function readMessageLanguageStore(): Record<string, Record<string, UiLanguage>> {
  const raw = localStorage.getItem(MESSAGE_LANGUAGE_STORAGE_KEY);
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw) as Record<string, Record<string, UiLanguage>>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeMessageLanguageStore(store: Record<string, Record<string, UiLanguage>>): void {
  localStorage.setItem(MESSAGE_LANGUAGE_STORAGE_KEY, JSON.stringify(store));
}

function summarizeRequirementUnderstanding(
  payload: unknown,
  fallback: string,
  labels: RequirementSummaryLabels,
): string {
  if (!payload || typeof payload !== "object") {
    return fallback;
  }

  const data = payload as Record<string, unknown>;
  const lines: string[] = [];

  const goal = typeof data.interpreted_goal === "string" ? data.interpreted_goal.trim() : "";
  if (goal) {
    lines.push(`${labels.goalLabel}: ${goal}`);
  }

  const explicit = Array.isArray(data.explicit_requirements) ? data.explicit_requirements : [];
  const explicitPairs: string[] = [];
  for (const row of explicit) {
    if (!row || typeof row !== "object") {
      continue;
    }
    const item = row as Record<string, unknown>;
    const reqType = typeof item.type === "string" ? item.type.trim() : "";
    const reqValue = typeof item.value === "string" ? item.value.trim() : "";
    if (!reqType || !reqValue) {
      continue;
    }
    explicitPairs.push(`${reqType}=${reqValue}`);
    if (explicitPairs.length >= 6) {
      break;
    }
  }
  if (explicitPairs.length > 0) {
    lines.push(`${labels.detectedLabel}: ${explicitPairs.join("; ")}`);
  }

  const missing = Array.isArray(data.missing_information) ? data.missing_information : [];
  const missingFields: string[] = [];
  for (const row of missing) {
    if (!row || typeof row !== "object") {
      continue;
    }
    const item = row as Record<string, unknown>;
    const field = typeof item.field === "string" ? item.field.trim() : "";
    const isBlocking = Boolean(item.is_blocking);
    if (!field) {
      continue;
    }
    missingFields.push(isBlocking ? `${field}(${labels.blockingTag})` : field);
    if (missingFields.length >= 5) {
      break;
    }
  }
  if (missingFields.length > 0) {
    lines.push(`${labels.pendingLabel}: ${missingFields.join("; ")}`);
  }

  const canContinue =
    typeof data.can_continue_with_draft_plan === "boolean" ? data.can_continue_with_draft_plan : true;
  lines.push(canContinue ? labels.draftAvailable : labels.needMoreInfo);

  if (!lines.length) {
    return fallback;
  }
  return lines.join("\n");
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function normalizeStepStatus(value: unknown): AgentStepStatus {
  const status = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (status === "running" || status === "in_progress" || status === "processing") {
    return "running";
  }
  if (status === "done" || status === "completed" || status === "success") {
    return "done";
  }
  if (status === "error" || status === "failed" || status === "failure") {
    return "error";
  }
  return "pending";
}

function parseSearchQueriesFromTasks(rawTasks: unknown): string[] {
  if (!Array.isArray(rawTasks)) {
    return [];
  }
  const queries: string[] = [];
  const seen = new Set<string>();

  for (const task of rawTasks) {
    const row = asRecord(task);
    if (!row) {
      continue;
    }
    const list = Array.isArray(row.queries) ? row.queries : [];
    for (const item of list) {
      if (typeof item !== "string") {
        continue;
      }
      const query = item.trim();
      if (!query) {
        continue;
      }
      const key = query.toLowerCase();
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      queries.push(query);
      if (queries.length >= 6) {
        return queries;
      }
    }
  }

  return queries;
}

function parseBackendAgentSteps(raw: unknown, fallbackDescription: string): AgentProgressStep[] | null {
  if (!Array.isArray(raw)) {
    return null;
  }

  const steps: AgentProgressStep[] = [];
  for (const item of raw) {
    const row = asRecord(item);
    if (!row) {
      continue;
    }

    const id = typeof row.id === "string" ? row.id.trim() : "";
    const title = typeof row.title === "string" ? row.title.trim() : "";
    if (!id || !title) {
      continue;
    }

    const description = typeof row.description === "string" ? row.description.trim() : "";
    const status = normalizeStepStatus(row.status);
    const queries = Array.isArray(row.queries)
      ? row.queries.filter((q): q is string => typeof q === "string" && q.trim().length > 0).slice(0, 6)
      : [];
    const filteredCount =
      typeof row.filteredCount === "number" && Number.isFinite(row.filteredCount)
        ? row.filteredCount
        : typeof row.filtered_count === "number" && Number.isFinite(row.filtered_count)
          ? row.filtered_count
          : null;
    const errorMessage =
      typeof row.errorMessage === "string"
        ? row.errorMessage.trim()
        : typeof row.error === "string"
          ? row.error.trim()
          : "";

    steps.push({
      id,
      title,
      description: description || fallbackDescription,
      status,
      queries,
      filteredCount,
      errorMessage: errorMessage || undefined,
    });
  }

  return steps.length ? steps : null;
}

function deriveAgentSteps(
  stage: AgentStage,
  running: boolean,
  searchQueries: string[],
  filteredCount: number | null,
  errorMessage: string | null,
  stepMeta: AgentStepMeta[],
): AgentProgressStep[] {
  const currentIndex = stageToStepIndex(stage);
  const doneAll = stage === "completed";

  return stepMeta.map((meta, idx) => {
    let status: AgentStepStatus = "pending";
    if (doneAll) {
      status = "done";
    } else if (idx < currentIndex) {
      status = "done";
    } else if (idx === currentIndex && running) {
      status = "running";
    } else if (idx === currentIndex && stage === "stopped") {
      status = "pending";
    }

    if (errorMessage && idx === currentIndex) {
      status = "error";
    }

    return {
      id: meta.id,
      title: meta.title,
      description: meta.description,
      status,
      queries: meta.id === "search" ? searchQueries : undefined,
      filteredCount: meta.id === "filter" ? filteredCount : undefined,
      errorMessage: status === "error" ? errorMessage ?? undefined : undefined,
    };
  });
}

export default function App() {
  const [uiLanguage, setUiLanguage] = useState<UiLanguage>(() => {
    const saved = localStorage.getItem(UI_LANGUAGE_KEY);
    return saved === "zh" || saved === "en" ? saved : "zh";
  });

  const [tokens, setTokens] = useState<Tokens | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [pendingDeleteConversationId, setPendingDeleteConversationId] = useState<string | null>(null);
  const [conversationBusyId, setConversationBusyId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messageInput, setMessageInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [agentSpeedMode, setAgentSpeedMode] = useState<"quality" | "fast">("quality");
  const [chatError, setChatError] = useState<string | null>(null);
  const [agentStage, setAgentStage] = useState<AgentStage>("idle");
  const [isReviewingHistory, setIsReviewingHistory] = useState(false);
  const [agentSearchQueries, setAgentSearchQueries] = useState<string[]>([]);
  const [agentFilteredCount, setAgentFilteredCount] = useState<number | null>(null);
  const [agentErrorMessage, setAgentErrorMessage] = useState<string | null>(null);
  const [backendAgentSteps, setBackendAgentSteps] = useState<AgentProgressStep[] | null>(null);
  const [thinkingTraceByMessageId, setThinkingTraceByMessageId] = useState<Record<string, ThinkingTrace>>({});
  const [messageLanguageById, setMessageLanguageById] = useState<Record<string, UiLanguage>>({});
  const [travelPlanByMessageId, setTravelPlanByMessageId] = useState<Record<string, TravelPlanPayload>>({});
  const [showMobileSidebar, setShowMobileSidebar] = useState(false);

  const chatBodyRef = useRef<HTMLElement | null>(null);
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const autoScrollEnabledRef = useRef(true);
  const activeStreamAbortRef = useRef<AbortController | null>(null);
  const pendingInputRef = useRef("");
  const latestTravelPlanRef = useRef<TravelPlanPayload | null>(null);
  const userStoppedRef = useRef(false);

  const t = UI_TEXT[uiLanguage];
  const visibleMessages = useMemo(
    () => messages.filter((item) => item.role === "assistant" || item.role === "user"),
    [messages],
  );
  const recentTravelPlans = useMemo(() => {
    const rows = [...conversations];
    rows.sort((a, b) => {
      const at = new Date(a.updated_at).getTime();
      const bt = new Date(b.updated_at).getTime();
      return bt - at;
    });
    return rows.slice(0, 4);
  }, [conversations]);
  const fallbackAgentSteps = useMemo(
    () =>
      deriveAgentSteps(
        agentStage,
        chatBusy,
        agentSearchQueries,
        agentFilteredCount,
        agentErrorMessage,
        t.agent.defaultSteps,
      ),
    [agentStage, chatBusy, agentSearchQueries, agentFilteredCount, agentErrorMessage, t.agent.defaultSteps],
  );
  const agentProgressSteps = backendAgentSteps && backendAgentSteps.length ? backendAgentSteps : fallbackAgentSteps;
  const agentProgressStepCount = agentProgressSteps.length;

  useEffect(() => {
    localStorage.setItem(UI_LANGUAGE_KEY, uiLanguage);
  }, [uiLanguage]);

  useEffect(() => {
    if (!selectedConversationId) {
      return;
    }
    const store = readThinkingTraceStore();
    store[selectedConversationId] = thinkingTraceByMessageId;
    writeThinkingTraceStore(store);
  }, [selectedConversationId, thinkingTraceByMessageId]);

  useEffect(() => {
    if (!selectedConversationId) {
      return;
    }
    const store = readMessageLanguageStore();
    store[selectedConversationId] = messageLanguageById;
    writeMessageLanguageStore(store);
  }, [selectedConversationId, messageLanguageById]);

  const applyTokens = (next: Tokens) => {
    setTokens(next);
    setStoredAuth(next);
  };

  const clearSession = () => {
    activeStreamAbortRef.current?.abort();
    activeStreamAbortRef.current = null;
    pendingInputRef.current = "";
    userStoppedRef.current = false;
    setTokens(null);
    setUser(null);
    setConversations([]);
    setSelectedConversationId(null);
    setRenamingConversationId(null);
    setRenameDraft("");
    setPendingDeleteConversationId(null);
    setConversationBusyId(null);
    setMessages([]);
    setChatBusy(false);
    setChatError(null);
    setAgentStage("idle");
    setIsReviewingHistory(false);
    setAgentSearchQueries([]);
    setAgentFilteredCount(null);
    setAgentErrorMessage(null);
    setBackendAgentSteps(null);
    setThinkingTraceByMessageId({});
    setMessageLanguageById({});
    setTravelPlanByMessageId({});
    setShowMobileSidebar(false);
    latestTravelPlanRef.current = null;
    clearStoredAuth();
  };

  const getAuthContext = (activeTokens: Tokens) => ({
    accessToken: activeTokens.access_token,
    refreshToken: activeTokens.refresh_token,
    onTokenRefresh: applyTokens,
  });

  useEffect(() => {
    const stored = getStoredAuth();
    if (stored?.tokens) {
      setTokens(stored.tokens);
    }
    setBooting(false);
  }, []);

  useEffect(() => {
    if (!tokens) {
      return;
    }

    let cancelled = false;
    const loadUser = async () => {
      try {
        const me = await getMe(getAuthContext(tokens));
        if (cancelled) {
          return;
        }
        setUser(me);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setAuthError(error instanceof Error ? error.message : t.auth.restoreFailed);
        clearSession();
      }
    };

    void loadUser();
    return () => {
      cancelled = true;
    };
  }, [tokens, t.auth.restoreFailed]);

  useEffect(() => {
    if (!tokens) {
      setConversations([]);
      setSelectedConversationId(null);
      return;
    }

    let cancelled = false;
    const loadConversations = async () => {
      try {
        const list = await listConversations(getAuthContext(tokens));
        if (cancelled) {
          return;
        }
        setConversations(list);
        setSelectedConversationId((current) => {
          if (current && list.some((item) => item.id === current)) {
            return current;
          }
          return list[0]?.id ?? null;
        });
      } catch (error) {
        if (!cancelled) {
          setChatError(error instanceof Error ? error.message : t.errors.loadConversations);
        }
      }
    };

    void loadConversations();
    return () => {
      cancelled = true;
    };
  }, [tokens, t.errors.loadConversations]);

  useEffect(() => {
    if (!tokens || !selectedConversationId) {
      setMessages([]);
      setThinkingTraceByMessageId({});
      setMessageLanguageById({});
      setTravelPlanByMessageId({});
      setAgentStage("idle");
      setIsReviewingHistory(false);
      setAgentSearchQueries([]);
      setAgentFilteredCount(null);
      setAgentErrorMessage(null);
      setBackendAgentSteps(null);
      autoScrollEnabledRef.current = true;
      return;
    }

    let cancelled = false;
    const loadMessages = async () => {
      try {
        const list = await listMessages(selectedConversationId, getAuthContext(tokens));
        if (cancelled) {
          return;
        }
        setMessages(list);
        const traceStore = readThinkingTraceStore();
        const savedTrace = traceStore[selectedConversationId] ?? {};
        setThinkingTraceByMessageId(savedTrace);
        const langStore = readMessageLanguageStore();
        const savedLang = langStore[selectedConversationId] ?? {};
        const inferred: Record<string, UiLanguage> = {};
        for (const msg of list) {
          if (msg.role !== "assistant") {
            continue;
          }
          inferred[msg.id] = savedLang[msg.id] ?? detectMessageLanguage(msg.content, uiLanguage);
        }
        setMessageLanguageById(inferred);
        setTravelPlanByMessageId({});
        latestTravelPlanRef.current = null;
      } catch (error) {
        if (!cancelled) {
          setChatError(error instanceof Error ? error.message : t.errors.loadMessages);
        }
      }
    };

    void loadMessages();
    return () => {
      cancelled = true;
    };
  }, [selectedConversationId, tokens, t.errors.loadMessages]);

  useEffect(() => {
    const body = chatBodyRef.current;
    if (!body) {
      return;
    }
    autoScrollEnabledRef.current = true;
    setIsReviewingHistory(false);
    body.scrollTop = body.scrollHeight;
  }, [selectedConversationId]);

  useEffect(() => {
    const body = chatBodyRef.current;
    if (!body) {
      return;
    }
    if (!autoScrollEnabledRef.current) {
      return;
    }
    body.scrollTop = body.scrollHeight;
  }, [messages, chatBusy]);

  useEffect(() => {
    const onKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowMobileSidebar(false);
      }
    };
    window.addEventListener("keydown", onKeydown);
    return () => window.removeEventListener("keydown", onKeydown);
  }, []);

  const toggleLanguage = () => {
    setUiLanguage((prev) => (prev === "zh" ? "en" : "zh"));
  };

  const handleAuthSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAuthBusy(true);
    setAuthError(null);

    try {
      if (authMode === "register") {
        await register(email.trim(), username.trim(), password);
      }
      const nextTokens = await login(email.trim(), password);
      applyTokens(nextTokens);
      setPassword("");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : t.auth.authFailed);
    } finally {
      setAuthBusy(false);
    }
  };

  const handleCreateConversation = async () => {
    if (!tokens) {
      return;
    }
    setChatError(null);

    try {
      const created = await createConversation(t.sidebar.newChatTitle, getAuthContext(tokens));
      setPendingDeleteConversationId(null);
      setConversations((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setSelectedConversationId(created.id);
      setShowMobileSidebar(false);
    } catch (error) {
      setChatError(error instanceof Error ? error.message : t.errors.createConversation);
    }
  };

  const handleSelectConversation = (conversationId: string) => {
    setSelectedConversationId(conversationId);
    setShowMobileSidebar(false);
    setTravelPlanByMessageId({});
    latestTravelPlanRef.current = null;
  };

  const handleSelectHomePrompt = (prompt: string) => {
    const text = prompt.trim();
    if (!text) {
      return;
    }
    setMessageInput(text);
    const input = composerInputRef.current;
    if (!input) {
      return;
    }
    input.focus();
    const end = text.length;
    window.requestAnimationFrame(() => {
      input.setSelectionRange(end, end);
    });
  };

  const handleChatScroll = () => {
    const body = chatBodyRef.current;
    if (!body) {
      return;
    }
    const nearBottom = isNearBottom(body);
    autoScrollEnabledRef.current = nearBottom;
    setIsReviewingHistory(!nearBottom);
  };

  const handleStartRename = (conversation: Conversation) => {
    setRenamingConversationId(conversation.id);
    setRenameDraft(conversation.title);
    setPendingDeleteConversationId(null);
  };

  const handleRenameConversation = async (conversationId: string) => {
    if (!tokens || !renameDraft.trim()) {
      return;
    }
    setConversationBusyId(conversationId);
    setChatError(null);
    try {
      const updated = await patchConversation(
        conversationId,
        { title: renameDraft.trim() },
        getAuthContext(tokens),
      );
      setConversations((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setRenamingConversationId(null);
      setRenameDraft("");
    } catch (error) {
      setChatError(error instanceof Error ? error.message : t.errors.renameConversation);
    } finally {
      setConversationBusyId(null);
    }
  };

  const handleRequestDeleteConversation = (conversationId: string) => {
    if (conversationBusyId) {
      return;
    }
    setPendingDeleteConversationId(conversationId);
    setRenamingConversationId(null);
    setRenameDraft("");
  };

  const handleDeleteConversation = async (conversation: Conversation) => {
    if (!tokens) {
      return;
    }
    setConversationBusyId(conversation.id);
    setChatError(null);
    try {
      await deleteConversation(conversation.id, getAuthContext(tokens));
      setConversations((prev) => {
        const next = prev.filter((item) => item.id !== conversation.id);
        if (selectedConversationId === conversation.id) {
          setSelectedConversationId(next[0]?.id ?? null);
          if (!next.length) {
            setMessages([]);
          }
        }
        return next;
      });
      if (pendingDeleteConversationId === conversation.id) {
        setPendingDeleteConversationId(null);
      }
    } catch (error) {
      setChatError(error instanceof Error ? error.message : t.errors.deleteConversation);
    } finally {
      setConversationBusyId(null);
    }
  };

  const handleLogout = async () => {
    if (tokens?.refresh_token) {
      try {
        await logout(tokens.refresh_token);
      } catch {
        // Best effort server-side logout.
      }
    }
    clearSession();
  };

  const handleStopMessage = () => {
    if (!chatBusy) {
      return;
    }
    userStoppedRef.current = true;
    activeStreamAbortRef.current?.abort();
    setAgentStage("stopped");
    setMessageInput(pendingInputRef.current);
    setThinkingTraceByMessageId((prev) => {
      const next = { ...prev };
      const now = Date.now();
      for (const [key, value] of Object.entries(next)) {
        if (value.status !== "running") {
          continue;
        }
        next[key] = {
          ...value,
          status: "stopped",
          endedAtMs: now,
          finalSummary: value.finalSummary || t.agent.userStoppedSummary,
          understanding: value.understanding || t.agent.userStoppedUnderstanding,
        };
      }
      return next;
    });
  };

  const handleSendMessage = async (overrideInput?: string) => {
    if (!tokens || chatBusy) {
      return;
    }

    const content = (overrideInput ?? messageInput).trim();
    if (!content) {
      return;
    }
    const responseLanguage = detectMessageLanguage(content, uiLanguage);

    pendingInputRef.current = content;
    userStoppedRef.current = false;
    autoScrollEnabledRef.current = true;
    setIsReviewingHistory(false);
    setChatBusy(true);
    setAgentStage("understanding");
    setAgentSearchQueries([]);
    setAgentFilteredCount(null);
    setAgentErrorMessage(null);
    setBackendAgentSteps(null);
    setChatError(null);
    latestTravelPlanRef.current = null;
    setShowMobileSidebar(false);
    setMessageInput("");

    let activeConversationId = selectedConversationId;
    let assistantLocalId = "";
    let streamCompleted = false;
    const streamAbortController = new AbortController();
    activeStreamAbortRef.current = streamAbortController;

    try {
      if (!activeConversationId) {
        const created = await createConversation(
          buildConversationTitle(content, t.sidebar.newChatTitle),
          getAuthContext(tokens),
        );
        activeConversationId = created.id;
        setConversations((prev) => [created, ...prev]);
        setSelectedConversationId(created.id);
      }

      if (!activeConversationId) {
        throw new Error(t.errors.missingConversationId);
      }
      const conversationId = activeConversationId;

      const userLocalId = `local-user-${Date.now()}`;
      assistantLocalId = `local-assistant-${Date.now()}`;
      const now = new Date().toISOString();
      const traceStartedAtMs = Date.now();
      const initialTraceText = TRACE_EVENT_TEXT[responseLanguage].start;

      setMessages((prev) => [
        ...prev,
        {
          id: userLocalId,
          conversation_id: conversationId,
          role: "user",
          content,
          created_at: now,
        },
        {
          id: assistantLocalId,
          conversation_id: conversationId,
          role: "assistant",
          content: "",
          created_at: now,
        },
      ]);
      setThinkingTraceByMessageId((prev) => ({
        ...prev,
        [assistantLocalId]: {
          startedAtMs: traceStartedAtMs,
          endedAtMs: null,
          status: "running",
          understanding: initialTraceText,
          plannerSummary: "",
          finalSummary: "",
          liveUpdates: [
            {
              id: `u-${traceStartedAtMs}-0`,
              text: initialTraceText,
              atMs: traceStartedAtMs,
            },
          ],
          searchQueries: [],
          searchRounds: [],
          sources: [],
        },
      }));
      setMessageLanguageById((prev) => ({
        ...prev,
        [assistantLocalId]: responseLanguage,
      }));
      const updateThinkingTrace = (updater: (current: ThinkingTrace) => ThinkingTrace) => {
        if (!assistantLocalId) {
          return;
        }
        setThinkingTraceByMessageId((prev) => {
          const current = prev[assistantLocalId];
          if (!current) {
            return prev;
          }
          return {
            ...prev,
            [assistantLocalId]: updater(current),
          };
        });
      };
      await streamMessage(
        conversationId,
        content,
        getAuthContext(tokens),
        (event) => {
          const eventLanguageRaw =
            typeof event.data.response_language === "string" ? event.data.response_language.trim().toLowerCase() : "";
          const eventLanguage: UiLanguage | null =
            eventLanguageRaw === "zh" || eventLanguageRaw === "en" ? (eventLanguageRaw as UiLanguage) : null;

          if (event.event === "heartbeat") {
            setAgentStage((prev) => (prev === "understanding" ? "searching" : prev));
            return;
          }

          if (event.event === "message_start") {
            setAgentStage("planning");
            const understanding =
              typeof event.data.understanding === "string" && event.data.understanding.trim()
                ? event.data.understanding.trim()
                : t.agent.messageStartFallback;
            const destination =
              typeof event.data.destination === "string" && event.data.destination.trim()
                ? event.data.destination.trim()
                : "";
            const days =
              typeof event.data.days === "number" && Number.isFinite(event.data.days) ? event.data.days : null;
            const detailParts: string[] = [understanding];
            if (destination) {
              detailParts.push(`destination: ${destination}`);
            }
            if (days && days > 0) {
              detailParts.push(`days: ${days}`);
            }
            const messageStartSummary =
              typeof event.data.input === "string" && event.data.input.trim()
                ? event.data.input.trim()
                : "";
            if (assistantLocalId && eventLanguage) {
              setMessageLanguageById((prev) => ({
                ...prev,
                [assistantLocalId]: eventLanguage,
              }));
            }
            updateThinkingTrace((current) => ({
              ...current,
              status: "running",
              understanding: detailParts.join("; "),
              plannerSummary: current.plannerSummary || messageStartSummary,
            }));
            return;
          }

          if (event.event === "planner") {
            setAgentStage("searching");
            const plannerData = asRecord(event.data);
            const plannerTasks = plannerData?.search_tasks;
            const plannedQueries = plannerTasks ? parseSearchQueriesFromTasks(plannerTasks) : [];
            if (plannerTasks) {
              setAgentSearchQueries(plannedQueries);
            }
            const plannerSteps =
              parseBackendAgentSteps(plannerData?.agentSteps, t.agent.processingDescription) ??
              parseBackendAgentSteps(plannerData?.agent_steps, t.agent.processingDescription);
            if (plannerSteps) {
              setBackendAgentSteps(plannerSteps);
            }
            const plannerUnderstanding =
              typeof event.data.understanding === "string" ? event.data.understanding.trim() : "";
            const requirementSummary = summarizeRequirementUnderstanding(
              event.data.requirement_understanding,
              plannerUnderstanding || t.agent.plannerFallback,
              t.agent.requirementLabels,
            );
            const plannerRounds = parseSearchRoundsFromDiagnostics(plannerData?.search_diagnostics);
            updateThinkingTrace((current) => ({
              ...current,
              understanding: requirementSummary,
              plannerSummary: plannerUnderstanding || current.plannerSummary,
              searchQueries: mergeUniqueQueries(current.searchQueries, plannedQueries),
              searchRounds: plannerRounds.length ? plannerRounds : current.searchRounds,
            }));
            return;
          }

          if (event.event === "structured_data") {
            const structuredRecord = asRecord(event.data);
            const structuredRoot = asRecord(structuredRecord?.json) ?? structuredRecord;
            const structuredRounds = parseSearchRoundsFromDiagnostics(structuredRecord?.search_diagnostics);
            const structuredSteps =
              parseBackendAgentSteps(structuredRoot?.agentSteps, t.agent.processingDescription) ??
              parseBackendAgentSteps(structuredRoot?.agent_steps, t.agent.processingDescription);
            if (structuredSteps) {
              setBackendAgentSteps(structuredSteps);
            }
            if (structuredRounds.length) {
              updateThinkingTrace((current) => ({
                ...current,
                searchRounds: structuredRounds,
              }));
            }
            const travelPlan = parseTravelPlanFromPayload(event.data);
            if (travelPlan) {
              latestTravelPlanRef.current = travelPlan;
              if (assistantLocalId) {
                setTravelPlanByMessageId((prev) => ({
                  ...prev,
                  [assistantLocalId]: travelPlan,
                }));
              }
            }
            return;
          }

          if (event.event === "sources") {
            setAgentStage("synthesizing");
            const sourceCount =
              typeof event.data.count === "number" && Number.isFinite(event.data.count)
                ? event.data.count
                : null;
            if (typeof sourceCount === "number") {
              setAgentFilteredCount(sourceCount);
            }
            const sourceData = asRecord(event.data);
            const sourceSteps =
              parseBackendAgentSteps(sourceData?.agentSteps, t.agent.processingDescription) ??
              parseBackendAgentSteps(sourceData?.agent_steps, t.agent.processingDescription);
            if (sourceSteps) {
              setBackendAgentSteps(sourceSteps);
            }

            const rawItems = Array.isArray(event.data.items) ? event.data.items : [];
            const parsed: TraceLink[] = [];
            for (const item of rawItems) {
              if (!item || typeof item !== "object") {
                continue;
              }
              const record = item as Record<string, unknown>;
              const url = typeof record.url === "string" ? record.url.trim() : "";
              if (!url) {
                continue;
              }
              parsed.push({
                title: typeof record.title === "string" ? record.title.trim() || url : url,
                url,
                platform: typeof record.platform === "string" ? record.platform.trim() : undefined,
                snippet: typeof record.snippet === "string" ? record.snippet.trim() : undefined,
              });
            }
            const nextSources = dedupeTraceSources(parsed);
            const sourceRounds = parseSearchRoundsFromDiagnostics(sourceData?.search_diagnostics);
            updateThinkingTrace((current) => ({
              ...current,
              understanding: current.understanding || t.agent.searchDoneFallback,
              sources: nextSources.length ? nextSources : current.sources,
              searchRounds: sourceRounds.length ? sourceRounds : current.searchRounds,
            }));
            return;
          }

          if (event.event === "tool_call") {
            const toolData = asRecord(event.data);
            const statusRaw = typeof toolData?.status === "string" ? toolData.status.trim().toLowerCase() : "";
            const query = typeof toolData?.query === "string" ? toolData.query.trim() : "";

            if (statusRaw === "running" || statusRaw === "started") {
              setAgentStage("searching");
            }
            if (query) {
              setAgentSearchQueries((prev) => {
                if (prev.some((item) => item.toLowerCase() === query.toLowerCase())) {
                  return prev;
                }
                return [...prev, query].slice(0, 6);
              });
              updateThinkingTrace((current) => ({
                ...current,
                searchQueries: mergeUniqueQueries(current.searchQueries, [query]),
              }));
            }
            if (statusRaw === "error" || statusRaw === "failed") {
              const message = typeof toolData?.error === "string" ? toolData.error.trim() : t.agent.toolFailed;
              setAgentErrorMessage(message);
              updateThinkingTrace((current) => ({
                ...current,
                status: "error",
                finalSummary: current.finalSummary || message,
              }));
            }
            return;
          }

          if (event.event === "agent_steps") {
            const stepPayload = asRecord(event.data);
            const steps =
              parseBackendAgentSteps(stepPayload?.agentSteps, t.agent.processingDescription) ??
              parseBackendAgentSteps(stepPayload?.agent_steps, t.agent.processingDescription) ??
              parseBackendAgentSteps(stepPayload?.steps, t.agent.processingDescription);
            if (steps) {
              setBackendAgentSteps(steps);
            }
            return;
          }

          if (event.event === "error") {
            const message = messageContentFromEvent(event);
            setAgentErrorMessage(message || t.agent.processFailed);
            updateThinkingTrace((current) => ({
              ...current,
              status: "error",
              endedAtMs: Date.now(),
              understanding: current.understanding || t.agent.flowErrorFallback,
              finalSummary: message || t.agent.flowErrorSummaryFallback,
            }));
            return;
          }

          if (event.event === "token") {
            setAgentStage("drafting");
            const tokenText = messageContentFromEvent(event);
            if (!tokenText) {
              return;
            }
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantLocalId ? { ...msg, content: `${msg.content}${tokenText}` } : msg,
              ),
            );
            return;
          }

          if (event.event === "message_end") {
            streamCompleted = true;
            setAgentStage("completed");
            setAgentErrorMessage(null);

            const finalText = messageContentFromEvent(event);
            if (assistantLocalId && finalText) {
              setMessageLanguageById((prev) => ({
                ...prev,
                [assistantLocalId]: eventLanguage ?? detectMessageLanguage(finalText, responseLanguage),
              }));
            }
            const endData = asRecord(event.data);
            const endRoot = asRecord(endData?.structured_response) ?? endData;
            const endRounds = parseSearchRoundsFromDiagnostics(endData?.search_diagnostics);
            const endSteps =
              parseBackendAgentSteps(endRoot?.agentSteps, t.agent.processingDescription) ??
              parseBackendAgentSteps(endRoot?.agent_steps, t.agent.processingDescription);
            if (endSteps) {
              setBackendAgentSteps(endSteps);
            }

            const travelPlan = parseTravelPlanFromPayload(event.data);
            if (travelPlan) {
              latestTravelPlanRef.current = travelPlan;
              if (assistantLocalId) {
                setTravelPlanByMessageId((prev) => ({
                  ...prev,
                  [assistantLocalId]: travelPlan,
                }));
              }
            }

            setMessages((prev) =>
              prev.map((msg) => (msg.id === assistantLocalId ? { ...msg, content: finalText } : msg)),
            );

            const finalSummary =
              typeof event.data.final_summary === "string" && event.data.final_summary.trim()
                ? event.data.final_summary.trim()
                : finalText.slice(0, 180);
            updateThinkingTrace((current) => ({
              ...current,
              status: "done",
              endedAtMs: Date.now(),
              understanding: current.understanding || t.agent.finalAnalyzedFallback,
              finalSummary,
              searchRounds: endRounds.length ? endRounds : current.searchRounds,
            }));
          }
        },
        {
          signal: streamAbortController.signal,
          agentSpeedMode,
        },
      );

      if (!userStoppedRef.current) {
        const [latestMessages, latestConversations] = await Promise.all([
          listMessages(conversationId, getAuthContext(tokens)),
          listConversations(getAuthContext(tokens)),
        ]);
        setMessages(latestMessages);
        const latestAssistantMessage = [...latestMessages].reverse().find((item) => item.role === "assistant");
        if (latestAssistantMessage && assistantLocalId) {
          setThinkingTraceByMessageId((prev) => {
            const localTrace = prev[assistantLocalId];
            if (!localTrace) {
              return prev;
            }
            const next = { ...prev };
            delete next[assistantLocalId];
            next[latestAssistantMessage.id] = {
              ...localTrace,
              status: localTrace.status === "running" ? "done" : localTrace.status,
              endedAtMs: localTrace.endedAtMs ?? Date.now(),
            };
            return next;
          });
          setMessageLanguageById((prev) => {
            const localLang = prev[assistantLocalId];
            const next = { ...prev };
            delete next[assistantLocalId];
            if (localLang) {
              next[latestAssistantMessage.id] = localLang;
            } else {
              const latestAssistantText = latestAssistantMessage.content || "";
              next[latestAssistantMessage.id] = detectMessageLanguage(latestAssistantText, responseLanguage);
            }
            return next;
          });
        }
        const latestPlan = latestTravelPlanRef.current;
        if (latestPlan) {
          const latestAssistantMessage = [...latestMessages].reverse().find((item) => item.role === "assistant");
          if (latestAssistantMessage) {
            setTravelPlanByMessageId((prev) => ({
              ...prev,
              [latestAssistantMessage.id]: latestPlan,
            }));
          }
        }
        setConversations(latestConversations);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : t.errors.sendMessage;
      const stoppedByUser = userStoppedRef.current || message.includes("stopped by user");

      if (stoppedByUser) {
        setAgentErrorMessage(null);
        if (assistantLocalId) {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantLocalId
                ? {
                    ...msg,
                    content: msg.content.trim()
                      ? `${msg.content}\n\n${t.agent.manualStoppedNote}`
                      : t.agent.manualStoppedNote,
                  }
                : msg,
            ),
          );
        }
        setChatError(null);
        if (assistantLocalId) {
          setThinkingTraceByMessageId((prev) => {
            const current = prev[assistantLocalId];
            if (!current) {
              return prev;
            }
            return {
              ...prev,
              [assistantLocalId]: {
                ...current,
                status: "stopped",
                endedAtMs: Date.now(),
                understanding: current.understanding || t.agent.userStoppedUnderstanding,
                finalSummary: current.finalSummary || t.agent.userStoppedSummary,
              },
            };
          });
        }
        setAgentStage("stopped");
      } else {
        setChatError(message);
        setAgentErrorMessage(message);
        setAgentStage("idle");
      }
    } finally {
      activeStreamAbortRef.current = null;
      setChatBusy(false);
      if (!userStoppedRef.current && streamCompleted) {
        pendingInputRef.current = "";
      }
    }
  };

  const handleComposerKey = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSendMessage();
    }
  };

  if (booting) {
    return <div className="page-shell center-note">{t.loading}</div>;
  }

  if (!tokens || !user) {
    return (
      <div className="page-shell auth-shell">
        <div className="ambient-shape ambient-left" />
        <div className="ambient-shape ambient-right" />
        <section className="auth-card">
          <div className="auth-top-row">
            <h1>{t.auth.title}</h1>
            <button className="ghost lang-pill" type="button" onClick={toggleLanguage}>
              {uiLanguage === "zh" ? "EN" : "中文"}
            </button>
          </div>
          <p>{t.auth.subtitle}</p>

          <div className="mode-toggle">
            <button className={authMode === "login" ? "active" : ""} onClick={() => setAuthMode("login")} type="button">
              {t.auth.login}
            </button>
            <button className={authMode === "register" ? "active" : ""} onClick={() => setAuthMode("register")} type="button">
              {t.auth.register}
            </button>
          </div>

          <form onSubmit={handleAuthSubmit} className="auth-form">
            <label htmlFor="email">{t.auth.email}</label>
            <input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />

            {authMode === "register" ? (
              <>
                <label htmlFor="username">{t.auth.username}</label>
                <input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  minLength={3}
                  required
                />
              </>
            ) : null}

            <label htmlFor="password">{t.auth.password}</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={8}
              required
            />

            {authError ? <div className="error-text">{authError}</div> : null}

            <button className="primary" type="submit" disabled={authBusy}>
              {authBusy ? t.auth.processing : authMode === "login" ? t.auth.login : t.auth.createAccount}
            </button>
          </form>
        </section>
      </div>
    );
  }

  return (
    <div className="page-shell app-shell">
      <div
        className={`sidebar-overlay ${showMobileSidebar ? "open" : ""}`}
        onClick={() => setShowMobileSidebar(false)}
        role="button"
        tabIndex={0}
      />

      <aside className={`sidebar ${showMobileSidebar ? "open" : ""}`}>
        <div className="brand-block">
          <h2>{t.appName}</h2>
          <p>{user.username}</p>
        </div>

        <button className="primary" type="button" onClick={handleCreateConversation}>
          {t.sidebar.newTrip}
        </button>

        <div className="conversation-list">
          {conversations.map((conversation) => (
            <div key={conversation.id} className="conversation-row">
              <button
                className={`conversation-item ${conversation.id === selectedConversationId ? "active" : ""}`}
                type="button"
                onClick={() => handleSelectConversation(conversation.id)}
              >
                <span>{conversation.title}</span>
              </button>

              <div className="conversation-actions">
                <button
                  type="button"
                  className="mini-btn"
                  onClick={() => handleStartRename(conversation)}
                  disabled={conversationBusyId === conversation.id}
                >
                  {t.sidebar.rename}
                </button>
                <button
                  type="button"
                  className="mini-btn"
                  onClick={() => handleRequestDeleteConversation(conversation.id)}
                  disabled={conversationBusyId === conversation.id}
                >
                  {t.sidebar.delete}
                </button>
              </div>

              {pendingDeleteConversationId === conversation.id ? (
                <div className="confirm-inline">
                  <button
                    className="mini-btn"
                    type="button"
                    onClick={() => handleDeleteConversation(conversation)}
                    disabled={conversationBusyId === conversation.id}
                  >
                    {t.sidebar.confirm}
                  </button>
                  <button
                    className="mini-btn"
                    type="button"
                    onClick={() => setPendingDeleteConversationId(null)}
                    disabled={conversationBusyId === conversation.id}
                  >
                    {t.sidebar.cancel}
                  </button>
                </div>
              ) : null}

              {renamingConversationId === conversation.id ? (
                <div className="rename-inline">
                  <input
                    value={renameDraft}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    placeholder={t.sidebar.newTitlePlaceholder}
                  />
                  <button
                    className="mini-btn"
                    type="button"
                    onClick={() => handleRenameConversation(conversation.id)}
                    disabled={conversationBusyId === conversation.id}
                  >
                    {t.sidebar.save}
                  </button>
                  <button
                    className="mini-btn"
                    type="button"
                    onClick={() => {
                      setRenamingConversationId(null);
                      setRenameDraft("");
                    }}
                  >
                    {t.sidebar.cancel}
                  </button>
                </div>
              ) : null}
            </div>
          ))}
          {!conversations.length ? <p className="empty-note">{t.sidebar.noConversation}</p> : null}
        </div>

        <RecentTravelPlans
          items={recentTravelPlans}
          selectedConversationId={selectedConversationId}
          onSelect={handleSelectConversation}
          title={t.sidebar.recentTitle}
          emptyText={t.sidebar.recentEmpty}
        />

        <button className="ghost" type="button" onClick={handleLogout}>
          {t.sidebar.logout}
        </button>
      </aside>

      <main className="chat-main">
        <header className="chat-header">
          <button
            type="button"
            className="menu-trigger"
            onClick={() => setShowMobileSidebar((prev) => !prev)}
            aria-label={t.header.toggleSidebar}
          >
            <span />
            <span />
            <span />
          </button>

          <div className="chat-header-left">
            <h3>{t.header.agentName}</h3>
            <p>{user.email}</p>
          </div>

          <div className="chat-header-actions" aria-label={t.header.languageSwitcher}>
            <button
              type="button"
              className={`lang-toggle-btn ${uiLanguage === "zh" ? "active" : ""}`}
              onClick={() => setUiLanguage("zh")}
            >
              中文
            </button>
            <button
              type="button"
              className={`lang-toggle-btn ${uiLanguage === "en" ? "active" : ""}`}
              onClick={() => setUiLanguage("en")}
            >
              EN
            </button>
          </div>
        </header>

        <section className="chat-body" ref={chatBodyRef} onScroll={handleChatScroll}>
          {chatBusy && isReviewingHistory ? <div className="scroll-review-hint">{t.agent.reviewingHint}</div> : null}

          <div className="chat-body-inner">
            {visibleMessages.map((message) => (
              <ChatMessageBubble
                key={message.id}
                message={message}
                isStreamingAssistant={chatBusy && message.role === "assistant"}
                timeLabel={formatTime(message.created_at)}
                language={
                  message.role === "assistant"
                    ? messageLanguageById[message.id] ?? detectMessageLanguage(message.content, uiLanguage)
                    : uiLanguage
                }
                travelPlan={message.role === "assistant" ? travelPlanByMessageId[message.id] ?? null : null}
                thinkingTrace={message.role === "assistant" ? thinkingTraceByMessageId[message.id] ?? null : null}
                labels={t.messageBubble}
              />
            ))}

            {!visibleMessages.length ? (
              <ChatEmptyState
                prompts={t.emptyState.prompts}
                onSelectPrompt={handleSelectHomePrompt}
                title={t.emptyState.title}
                subtitle={t.emptyState.subtitle}
                ariaLabel={t.emptyState.ariaLabel}
              />
            ) : null}
          </div>
        </section>

        <section className="composer-wrap" data-progress-steps={agentProgressStepCount}>
          {chatError ? <div className="error-text">{chatError}</div> : null}

          <div className="run-mode-toggle" role="group" aria-label={t.runMode.ariaLabel}>
            <button
              type="button"
              className={`run-mode-btn ${agentSpeedMode === "quality" ? "active" : ""}`}
              onClick={() => setAgentSpeedMode("quality")}
              disabled={chatBusy}
            >
              {t.runMode.quality}
            </button>
            <button
              type="button"
              className={`run-mode-btn ${agentSpeedMode === "fast" ? "active" : ""}`}
              onClick={() => setAgentSpeedMode("fast")}
              disabled={chatBusy}
            >
              {t.runMode.fast}
            </button>
          </div>

          <div className="composer">
            <textarea
              ref={composerInputRef}
              value={messageInput}
              onChange={(event) => setMessageInput(event.target.value)}
              onKeyDown={handleComposerKey}
              placeholder={t.composer.placeholder}
              disabled={chatBusy}
            />
            <button
              className={`primary ${chatBusy ? "danger" : ""}`}
              type="button"
              onClick={chatBusy ? handleStopMessage : () => void handleSendMessage()}
              disabled={!chatBusy && !messageInput.trim()}
            >
              {chatBusy ? t.composer.stop : t.composer.send}
            </button>
          </div>

          {visibleMessages.length ? (
            <div className="quick-row">
              {t.quickPrompts.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="quick-chip"
                  disabled={chatBusy}
                  onClick={() => setMessageInput(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
