import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";

import {
  clearStoredAuth,
  createConversation,
  getMe,
  getStoredAuth,
  listConversations,
  listMessages,
  login,
  logout,
  register,
  setStoredAuth,
  streamMessage,
} from "./lib/api";
import type { Conversation, Message, StreamEvent, Tokens, User } from "./types";

function formatTime(input: string): string {
  return new Date(input).toLocaleString([], { hour: "2-digit", minute: "2-digit" });
}

function buildConversationTitle(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    return "New travel chat";
  }
  return trimmed.length > 28 ? `${trimmed.slice(0, 28)}...` : trimmed;
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

export default function App() {
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
  const [messages, setMessages] = useState<Message[]>([]);
  const [messageInput, setMessageInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [streamLogs, setStreamLogs] = useState<string[]>([]);

  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );

  const applyTokens = (next: Tokens) => {
    setTokens(next);
    setStoredAuth(next);
  };

  const clearSession = () => {
    setTokens(null);
    setUser(null);
    setConversations([]);
    setSelectedConversationId(null);
    setMessages([]);
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
    const loadUserAndConversations = async () => {
      try {
        const auth = getAuthContext(tokens);
        const me = await getMe(auth);
        if (cancelled) {
          return;
        }
        setUser(me);

        const list = await listConversations(auth);
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
        if (cancelled) {
          return;
        }
        setAuthError(error instanceof Error ? error.message : "Failed to restore session.");
        clearSession();
      }
    };

    loadUserAndConversations();
    return () => {
      cancelled = true;
    };
  }, [tokens]);

  useEffect(() => {
    if (!tokens || !selectedConversationId) {
      setMessages([]);
      return;
    }

    let cancelled = false;
    const loadMessages = async () => {
      try {
        const list = await listMessages(selectedConversationId, getAuthContext(tokens));
        if (!cancelled) {
          setMessages(list);
        }
      } catch (error) {
        if (!cancelled) {
          setChatError(error instanceof Error ? error.message : "Failed to load messages.");
        }
      }
    };

    loadMessages();
    return () => {
      cancelled = true;
    };
  }, [selectedConversationId, tokens]);

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
      setAuthError(error instanceof Error ? error.message : "Authentication failed.");
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
      const created = await createConversation("New travel chat", getAuthContext(tokens));
      setConversations((prev) => [created, ...prev]);
      setSelectedConversationId(created.id);
    } catch (error) {
      setChatError(error instanceof Error ? error.message : "Failed to create conversation.");
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

  const pushStreamLog = (line: string) => {
    setStreamLogs((prev) => {
      const next = [...prev, line];
      return next.slice(-6);
    });
  };

  const handleSendMessage = async () => {
    if (!tokens || chatBusy) {
      return;
    }

    const content = messageInput.trim();
    if (!content) {
      return;
    }

    setChatBusy(true);
    setChatError(null);
    setStreamLogs([]);
    setMessageInput("");

    let activeConversationId = selectedConversationId;
    try {
      if (!activeConversationId) {
        const created = await createConversation(buildConversationTitle(content), getAuthContext(tokens));
        activeConversationId = created.id;
        setConversations((prev) => [created, ...prev]);
        setSelectedConversationId(created.id);
      }

      if (!activeConversationId) {
        throw new Error("Conversation ID is missing.");
      }
      const conversationId = activeConversationId;

      const userLocalId = `local-user-${Date.now()}`;
      const assistantLocalId = `local-assistant-${Date.now()}`;
      const now = new Date().toISOString();

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

      await streamMessage(conversationId, content, getAuthContext(tokens), (event) => {
        if (event.event === "planner") {
          const count = typeof event.data.plan_count === "number" ? event.data.plan_count : "?";
          pushStreamLog(`Planner generated ${count} steps`);
          return;
        }

        if (event.event === "tool_call") {
          const tool = typeof event.data.tool === "string" ? event.data.tool : "unknown";
          const status = typeof event.data.status === "string" ? event.data.status : "running";
          pushStreamLog(`Tool ${tool}: ${status}`);
          return;
        }

        if (event.event === "error") {
          const message = messageContentFromEvent(event);
          pushStreamLog(`Pipeline fallback: ${message || "unknown error"}`);
          return;
        }

        if (event.event === "token") {
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
          const finalText = messageContentFromEvent(event);
          setMessages((prev) =>
            prev.map((msg) => (msg.id === assistantLocalId ? { ...msg, content: finalText } : msg)),
          );
        }
      });

      const [latestMessages, latestConversations] = await Promise.all([
        listMessages(conversationId, getAuthContext(tokens)),
        listConversations(getAuthContext(tokens)),
      ]);
      setMessages(latestMessages);
      setConversations(latestConversations);
    } catch (error) {
      setChatError(error instanceof Error ? error.message : "Failed to send message.");
    } finally {
      setChatBusy(false);
    }
  };

  const handleComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSendMessage();
    }
  };

  if (booting) {
    return <div className="page-shell center-note">Loading Tourist Agent...</div>;
  }

  if (!tokens || !user) {
    return (
      <div className="page-shell auth-shell">
        <div className="ambient-shape ambient-left" />
        <div className="ambient-shape ambient-right" />
        <section className="auth-card">
          <h1>Tourist Agent</h1>
          <p>Plan your next journey with a live travel assistant.</p>

          <div className="mode-toggle">
            <button
              className={authMode === "login" ? "active" : ""}
              onClick={() => setAuthMode("login")}
              type="button"
            >
              Login
            </button>
            <button
              className={authMode === "register" ? "active" : ""}
              onClick={() => setAuthMode("register")}
              type="button"
            >
              Register
            </button>
          </div>

          <form onSubmit={handleAuthSubmit} className="auth-form">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />

            {authMode === "register" ? (
              <>
                <label htmlFor="username">Username</label>
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

            <label htmlFor="password">Password</label>
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
              {authBusy ? "Processing..." : authMode === "login" ? "Login" : "Create account"}
            </button>
          </form>
        </section>
      </div>
    );
  }

  return (
    <div className="page-shell app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <h2>Tourist Agent</h2>
          <p>{user.username}</p>
        </div>

        <button className="primary" type="button" onClick={handleCreateConversation}>
          + New Chat
        </button>

        <div className="conversation-list">
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              className={`conversation-item ${conversation.id === selectedConversationId ? "active" : ""}`}
              type="button"
              onClick={() => setSelectedConversationId(conversation.id)}
            >
              <span>{conversation.title}</span>
            </button>
          ))}
          {!conversations.length ? <p className="empty-note">No conversation yet.</p> : null}
        </div>

        <button className="ghost" type="button" onClick={handleLogout}>
          Logout
        </button>
      </aside>

      <main className="chat-main">
        <header className="chat-header">
          <h3>{selectedConversation?.title ?? "Create a chat to start planning"}</h3>
          <p>{user.email}</p>
        </header>

        <section className="chat-body">
          {messages.map((message) => (
            <article key={message.id} className={`bubble ${message.role === "user" ? "user" : "assistant"}`}>
              <div className="bubble-role">{message.role === "user" ? "You" : "Assistant"}</div>
              <div className="bubble-content">{message.content || (chatBusy ? "Typing..." : "")}</div>
              <div className="bubble-time">{formatTime(message.created_at)}</div>
            </article>
          ))}
          {!messages.length ? (
            <div className="empty-chat">
              Ask for routes, hotels, local food, budgets, visa checklists, or day-by-day itineraries.
            </div>
          ) : null}
        </section>

        <section className="composer-wrap">
          {streamLogs.length ? (
            <div className="stream-log">
              {streamLogs.map((line) => (
                <span key={line}>{line}</span>
              ))}
            </div>
          ) : null}

          {chatError ? <div className="error-text">{chatError}</div> : null}

          <div className="composer">
            <textarea
              value={messageInput}
              onChange={(event) => setMessageInput(event.target.value)}
              onKeyDown={handleComposerKey}
              placeholder="Tell me your destination, dates, budget and interests..."
              disabled={chatBusy}
            />
            <button className="primary" type="button" onClick={handleSendMessage} disabled={chatBusy}>
              {chatBusy ? "Sending..." : "Send"}
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
