import {
  normalizeSearchTask,
  parseSearchTaskList,
  shouldRequireHighFreshness,
  type SearchTask,
  type SearchTaskType,
} from "../../schemas/searchTask.schema";
import type { LlmJsonClient } from "./_llm";
import { toJsonText } from "./_llm";

export interface PlanSearchTasksInput {
  user_input: string;
  conversation_context?: unknown;
  user_need_understanding?: unknown;
  llm: LlmJsonClient;
}

const SYSTEM_PROMPT = `You are a travel search planning agent.
Plan precision search tasks instead of answering the user.
Output strict JSON with key: search_tasks.

Rules:
1) Split travel information needs into separate tasks: attraction, restaurant, hotel_area, transport, weather, guide.
2) For opening hours, ticket price, weather, transport, set freshness_required="high".
3) Every task must include: task_id, information_need, why_needed, type, priority, freshness_required, acceptance_criteria.
4) If destination is missing/unclear, include destination_recommendation task and acknowledge uncertainty.
5) Avoid generic or vague tasks.`;

function hasDestination(understanding: unknown): boolean {
  if (!understanding || typeof understanding !== "object") {
    return false;
  }
  const explicit = (understanding as { explicit_requirements?: unknown }).explicit_requirements;
  if (!Array.isArray(explicit)) {
    return false;
  }
  return explicit.some((row) => {
    if (!row || typeof row !== "object") {
      return false;
    }
    const record = row as { type?: unknown; value?: unknown };
    const type = typeof record.type === "string" ? record.type.trim().toLowerCase() : "";
    const value = typeof record.value === "string" ? record.value.trim() : "";
    return type.includes("destination") && value.length > 0;
  });
}

function ensureCoreTourismCoverage(tasks: SearchTask[], destinationKnown: boolean): SearchTask[] {
  const byType = new Map<SearchTaskType, SearchTask>();
  for (const task of tasks) {
    byType.set(task.type, task);
  }

  const required: SearchTaskType[] = destinationKnown
    ? ["attraction", "restaurant", "hotel_area", "transport", "weather", "guide"]
    : ["destination_recommendation", "transport", "weather", "guide"];

  const patched = [...tasks];
  let nextIndex = patched.length + 1;

  for (const type of required) {
    if (byType.has(type)) {
      continue;
    }
    patched.push(
      normalizeSearchTask(
        {
          task_id: `task_${nextIndex}`,
          information_need: `Travel evidence for ${type}`,
          why_needed: "Coverage gap detected in planner output",
          type,
          priority: type === "transport" || type === "weather" ? "high" : "medium",
          freshness_required: shouldRequireHighFreshness(
            normalizeSearchTask({
              task_id: "tmp",
              information_need: "tmp",
              why_needed: "tmp",
              type,
              priority: "medium",
              freshness_required: "medium",
              acceptance_criteria: [],
            }),
          )
            ? "high"
            : "medium",
          acceptance_criteria: ["At least 2 independent sources"],
        },
        nextIndex - 1,
      ),
    );
    nextIndex += 1;
  }

  return patched;
}

export async function planSearchTasks(input: PlanSearchTasksInput): Promise<SearchTask[]> {
  const payload = {
    user_input: input.user_input,
    conversation_context: input.conversation_context ?? [],
    user_need_understanding: input.user_need_understanding ?? {},
  };

  const llmOutput = await input.llm.generateJson<{ search_tasks?: unknown }>({
    systemPrompt: SYSTEM_PROMPT,
    userPrompt: toJsonText(payload),
    temperature: 0.1,
    maxTokens: 2200,
  });

  const parsed = parseSearchTaskList(llmOutput);
  const normalized = parsed.map((task, index) => normalizeSearchTask(task, index));
  const destinationKnown = hasDestination(input.user_need_understanding);
  return ensureCoreTourismCoverage(normalized, destinationKnown);
}
