export interface LlmJsonRequest {
  systemPrompt: string;
  userPrompt: string;
  temperature?: number;
  maxTokens?: number;
}

export interface LlmJsonClient {
  generateJson<T>(request: LlmJsonRequest): Promise<T>;
}

export function toJsonText(payload: unknown): string {
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}
