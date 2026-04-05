export type ApiEnvelope<T> = {
  success: boolean;
  data: T;
  error: { code?: string; message?: string } | null;
};

export type Tokens = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
};

export type User = {
  user_id: string;
  email: string;
  username: string;
  is_active?: boolean;
  created_at: string;
};

export type Conversation = {
  id: string;
  title: string;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
};

export type StreamEvent = {
  event: string;
  data: Record<string, unknown>;
};
