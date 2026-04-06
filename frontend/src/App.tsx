import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";

import {
  clearStoredAuth,
  createConversation,
  getMe,
  getPreferences,
  getStoredAuth,
  listConversations,
  listMessages,
  login,
  logout,
  patchPreferences,
  register,
  setStoredAuth,
  streamMessage,
} from "./lib/api";
import type { Conversation, Message, Preference, StreamEvent, Tokens, User } from "./types";

type PreferenceForm = {
  language: string;
  timezone: string;
  budget_level: string;
  interests_text: string;
  dietary_text: string;
  mobility_notes: string;
};

const DEFAULT_PREFERENCE_FORM: PreferenceForm = {
  language: "en",
  timezone: "UTC",
  budget_level: "medium",
  interests_text: "",
  dietary_text: "",
  mobility_notes: "",
};

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

function splitCsv(input: string): string[] {
  return input
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function toPreferenceForm(pref: Preference | null): PreferenceForm {
  if (!pref) {
    return DEFAULT_PREFERENCE_FORM;
  }
  return {
    language: pref.language || "en",
    timezone: pref.timezone || "UTC",
    budget_level: pref.budget_level || "medium",
    interests_text: (pref.interests || []).join(", "),
    dietary_text: (pref.dietary || []).join(", "),
    mobility_notes: pref.mobility_notes || "",
  };
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

  const [showPreferences, setShowPreferences] = useState(false);
  const [preferences, setPreferences] = useState<Preference | null>(null);
  const [preferenceForm, setPreferenceForm] = useState<PreferenceForm>(DEFAULT_PREFERENCE_FORM);
  const [prefBusy, setPrefBusy] = useState(false);
  const [prefError, setPrefError] = useState<string | null>(null);
  const [prefSuccess, setPrefSuccess] = useState<string | null>(null);

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
    setPreferences(null);
    setPreferenceForm(DEFAULT_PREFERENCE_FORM);
    setShowPreferences(false);
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
        const [me, list, pref] = await Promise.all([
          getMe(auth),
          listConversations(auth),
          getPreferences(auth),
        ]);
        if (cancelled) {
          return;
        }

        setUser(me);
        setConversations(list);
        setSelectedConversationId((current) => {
          if (current && list.some((item) => item.id === current)) {
            return current;
          }
          return list[0]?.id ?? null;
        });
        setPreferences(pref);
        setPreferenceForm(toPreferenceForm(pref));
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

  const handleSavePreferences = async () => {
    if (!tokens || prefBusy) {
      return;
    }
    setPrefBusy(true);
    setPrefError(null);
    setPrefSuccess(null);

    try {
      const payload = {
        language: preferenceForm.language.trim() || "en",
        timezone: preferenceForm.timezone.trim() || "UTC",
        budget_level: preferenceForm.budget_level.trim() || "medium",
        interests: splitCsv(preferenceForm.interests_text),
        dietary: splitCsv(preferenceForm.dietary_text),
        mobility_notes: preferenceForm.mobility_notes.trim() || null,
      };

      const saved = await patchPreferences(payload, getAuthContext(tokens));
      setPreferences(saved);
      setPreferenceForm(toPreferenceForm(saved));
      setPrefSuccess("Preferences saved.");
    } catch (error) {
      setPrefError(error instanceof Error ? error.message : "Failed to save preferences.");
    } finally {
      setPrefBusy(false);
    }
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
        if (event.event === "message_start") {
          const applied = event.data.preferences_applied === true;
          if (applied) {
            pushStreamLog("Personalization profile applied");
          }
          return;
        }

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

        <button className="ghost" type="button" onClick={() => setShowPreferences(true)}>
          Preferences
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

      <div
        className={`prefs-overlay ${showPreferences ? "open" : ""}`}
        onClick={() => setShowPreferences(false)}
        role="button"
        tabIndex={0}
      />
      <aside className={`prefs-drawer ${showPreferences ? "open" : ""}`}>
        <div className="prefs-header">
          <h3>Travel Preferences</h3>
          <button className="ghost" type="button" onClick={() => setShowPreferences(false)}>
            Close
          </button>
        </div>

        <div className="prefs-form">
          <label htmlFor="pref-language">Language</label>
          <input
            id="pref-language"
            value={preferenceForm.language}
            onChange={(event) => setPreferenceForm((prev) => ({ ...prev, language: event.target.value }))}
          />

          <label htmlFor="pref-timezone">Timezone</label>
          <input
            id="pref-timezone"
            value={preferenceForm.timezone}
            onChange={(event) => setPreferenceForm((prev) => ({ ...prev, timezone: event.target.value }))}
          />

          <label htmlFor="pref-budget">Budget Level</label>
          <select
            id="pref-budget"
            value={preferenceForm.budget_level}
            onChange={(event) => setPreferenceForm((prev) => ({ ...prev, budget_level: event.target.value }))}
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>

          <label htmlFor="pref-interests">Interests (comma separated)</label>
          <input
            id="pref-interests"
            value={preferenceForm.interests_text}
            onChange={(event) => setPreferenceForm((prev) => ({ ...prev, interests_text: event.target.value }))}
            placeholder="food, anime, museums, hiking"
          />

          <label htmlFor="pref-dietary">Dietary (comma separated)</label>
          <input
            id="pref-dietary"
            value={preferenceForm.dietary_text}
            onChange={(event) => setPreferenceForm((prev) => ({ ...prev, dietary_text: event.target.value }))}
            placeholder="vegetarian, halal"
          />

          <label htmlFor="pref-mobility">Mobility Notes</label>
          <textarea
            id="pref-mobility"
            value={preferenceForm.mobility_notes}
            onChange={(event) => setPreferenceForm((prev) => ({ ...prev, mobility_notes: event.target.value }))}
            placeholder="stairs should be minimized"
          />

          {prefError ? <div className="error-text">{prefError}</div> : null}
          {prefSuccess ? <div className="ok-text">{prefSuccess}</div> : null}

          <button className="primary" type="button" onClick={handleSavePreferences} disabled={prefBusy}>
            {prefBusy ? "Saving..." : "Save Preferences"}
          </button>

          <p className="prefs-meta">
            Last update: {preferences?.updated_at ? new Date(preferences.updated_at).toLocaleString() : "N/A"}
          </p>
        </div>
      </aside>
    </div>
  );
}
