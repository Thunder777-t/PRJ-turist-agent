import type { ApiEnvelope, Conversation, Message, StreamEvent, Tokens, User } from "../types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";
const AUTH_KEY = "tourist_agent_auth";

type StoredAuth = {
  tokens: Tokens;
};

export function getStoredAuth(): StoredAuth | null {
  const raw = localStorage.getItem(AUTH_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as StoredAuth;
  } catch {
    return null;
  }
}

export function setStoredAuth(tokens: Tokens): void {
  const value: StoredAuth = { tokens };
  localStorage.setItem(AUTH_KEY, JSON.stringify(value));
}

export function clearStoredAuth(): void {
  localStorage.removeItem(AUTH_KEY);
}

async function parseEnvelope<T>(response: Response): Promise<T> {
  const body = (await response.json()) as ApiEnvelope<T>;
  if (!response.ok || !body.success) {
    throw new Error(body.error?.message || `Request failed with ${response.status}`);
  }
  return body.data;
}

async function refreshTokens(refreshToken: string): Promise<Tokens> {
  const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  return parseEnvelope<Tokens>(response);
}

type AuthContext = {
  accessToken: string;
  refreshToken: string;
  onTokenRefresh: (tokens: Tokens) => void;
};

async function authorizedFetch(
  path: string,
  init: RequestInit,
  auth: AuthContext,
  stream = false,
): Promise<Response> {
  const attempt = async (token: string): Promise<Response> => {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    if (!stream && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    return fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  };

  let response = await attempt(auth.accessToken);
  if (response.status !== 401) {
    return response;
  }

  const nextTokens = await refreshTokens(auth.refreshToken);
  auth.onTokenRefresh(nextTokens);
  response = await attempt(nextTokens.access_token);
  return response;
}

export async function register(email: string, username: string, password: string): Promise<User> {
  const response = await fetch(`${API_BASE_URL}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username, password }),
  });
  return parseEnvelope<User>(response);
}

export async function login(email: string, password: string): Promise<Tokens> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return parseEnvelope<Tokens>(response);
}

export async function logout(refreshToken: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!response.ok) {
    throw new Error(`Logout failed with ${response.status}`);
  }
}

export async function getMe(auth: AuthContext): Promise<User> {
  const response = await authorizedFetch("/me", { method: "GET" }, auth);
  return parseEnvelope<User>(response);
}

export async function listConversations(auth: AuthContext): Promise<Conversation[]> {
  const response = await authorizedFetch("/conversations?limit=100", { method: "GET" }, auth);
  return parseEnvelope<Conversation[]>(response);
}

export async function createConversation(title: string, auth: AuthContext): Promise<Conversation> {
  const response = await authorizedFetch(
    "/conversations",
    {
      method: "POST",
      body: JSON.stringify({ title }),
    },
    auth,
  );
  return parseEnvelope<Conversation>(response);
}

export async function listMessages(conversationId: string, auth: AuthContext): Promise<Message[]> {
  const response = await authorizedFetch(
    `/conversations/${conversationId}/messages?limit=200`,
    { method: "GET" },
    auth,
  );
  return parseEnvelope<Message[]>(response);
}

function parseEvent(raw: string): StreamEvent | null {
  const lines = raw
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0);
  if (!lines.length) {
    return null;
  }

  let event = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }

  let parsed: Record<string, unknown> = {};
  const payload = dataLines.join("\n");
  if (payload) {
    try {
      parsed = JSON.parse(payload) as Record<string, unknown>;
    } catch {
      parsed = { raw: payload };
    }
  }
  return { event, data: parsed };
}

export async function streamMessage(
  conversationId: string,
  content: string,
  auth: AuthContext,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await authorizedFetch(
    `/conversations/${conversationId}/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
    auth,
    true,
  );

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Stream request failed with ${response.status}`);
  }

  if (!response.body) {
    throw new Error("Streaming response body is missing.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex >= 0) {
      const block = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      const parsed = parseEvent(block);
      if (parsed) {
        onEvent(parsed);
      }
      separatorIndex = buffer.indexOf("\n\n");
    }
  }

  if (buffer.trim()) {
    const parsed = parseEvent(buffer);
    if (parsed) {
      onEvent(parsed);
    }
  }
}
