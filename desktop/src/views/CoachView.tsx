/**
 * Coach — faithful port of the §14.10 advice-only, citation-grounded view.
 *
 * Similar to Agent Chat but with CitationChip discipline: parses
 * 〔record_type id_or_filter〕 patterns from assistant text and renders them
 * inline as CitationChip components.
 * No useEffect — useQuery + useMutation only.
 */
import { useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CitationChip } from "../components/badges";
import { apiFetch, waitForSidecar, apiBaseUrl } from "../lib/api";
import "./AgentChat.css";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Conversation {
  id: number;
  title: string | null;
  context_seed: string | null;
  created_at: string;
}

interface Message {
  id: number;
  role: string;
  content: string | null;
  tool_calls_json: string | null;
  tool_results_json?: string | null;
  evidence_citations_json?: string | null;
  model: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
}

interface MessagesResponse {
  conversation_id: number;
  messages: Message[];
}

interface SendMessageVariables {
  conversationId: number;
  text: string;
}

const VISIBLE_CHUNK_WINDOW = 60;

// ---------------------------------------------------------------------------
// Citation parser — finds 〔record_type id_or_filter〕 in text
// ---------------------------------------------------------------------------
const CITATION_RE = /〔([^\s〕]+)\s+([^〕]+)〕/g;

function renderWithCitations(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  // Reset lastIndex for global regex
  CITATION_RE.lastIndex = 0;

  while ((match = CITATION_RE.exec(text)) !== null) {
    // Add text before the citation
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    // Add the citation chip
    parts.push(
      <CitationChip
        key={`${match.index}-${match[1]}`}
        recordType={match[1]}
        idOrFilter={match[2]}
      />,
    );
    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}

function formatTokens(input?: number | null, output?: number | null): string | null {
  if (!input && !output) return null;
  const parts: string[] = [];
  if (input) parts.push(`${input.toLocaleString()}↓`);
  if (output) parts.push(`${output.toLocaleString()}↑`);
  return parts.join(" ");
}

function renderStreamingText(chunks: string[]): ReactNode {
  const historicalText =
    chunks.length > VISIBLE_CHUNK_WINDOW
      ? chunks.slice(0, -VISIBLE_CHUNK_WINDOW).join("")
      : "";
  const recentChunks =
    chunks.length > VISIBLE_CHUNK_WINDOW
      ? chunks.slice(-VISIBLE_CHUNK_WINDOW)
      : chunks;
  const offset = chunks.length - recentChunks.length;

  return (
    <>
      {historicalText && <span>{renderWithCitations(historicalText)}</span>}
      {recentChunks.map((chunk, index) => (
        <span key={offset + index} className="agent-stream-chunk">
          {renderWithCitations(chunk)}
        </span>
      ))}
    </>
  );
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const CoachView = () => {
  const qc = useQueryClient();
  const [activeConvoId, setActiveConvoId] = useState<number | null>(null);
  const [inputText, setInputText] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [streamChunks, setStreamChunks] = useState<string[]>([]);
  const [thinkingText, setThinkingText] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollAnchorRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = (smooth = false) => {
    requestAnimationFrame(() => {
      scrollAnchorRef.current?.scrollIntoView({
        behavior: smooth ? "smooth" : "instant",
        block: "end",
      });
    });
  };

  // List coach conversations (context_seed='coach').
  const { data: convosData } = useQuery({
    queryKey: ["agent-conversations"],
    queryFn: () =>
      apiFetch<{ conversations: Conversation[] }>("/agent/conversations"),
    retry: 1,
  });

  const coachConvos = (convosData?.conversations ?? []).filter(
    (c) => c.context_seed === "coach",
  );

  // Messages for active conversation.
  const { data: messagesData } = useQuery({
    queryKey: ["agent-messages", activeConvoId],
    queryFn: () =>
      apiFetch<MessagesResponse>(
        `/agent/conversations/${activeConvoId}/messages`,
      ),
    enabled: activeConvoId != null,
    retry: 1,
  });

  // Create coach conversation.
  const createConvo = useMutation({
    mutationFn: () =>
      apiFetch<{ conversation_id: number }>("/agent/conversations", {
        method: "POST",
        body: JSON.stringify({ title: "Coach session", context_seed: "coach" }),
      }),
    onSuccess: (data) => {
      setActiveConvoId(data.conversation_id);
      setDeleteConfirmId(null);
      setStreamChunks([]);
      setThinkingText(null);
      setStreamError(null);
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
  });

  const deleteConvo = useMutation({
    mutationFn: (conversationId: number) =>
      apiFetch<{ ok: boolean; conversation_id: number }>(
        `/agent/conversations/${conversationId}`,
        { method: "DELETE" },
      ),
    onSuccess: (_data, conversationId) => {
      if (activeConvoId === conversationId) {
        setActiveConvoId(null);
        setStreamChunks([]);
        setThinkingText(null);
        setStreamError(null);
      }
      setDeleteConfirmId(null);
      qc.removeQueries({ queryKey: ["agent-messages", conversationId] });
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
  });

  // Send message.
  const sendMessage = useMutation({
    mutationFn: async ({ conversationId, text }: SendMessageVariables) => {
      const info = await waitForSidecar();
      const url = `${apiBaseUrl(info)}/agent/conversations/${conversationId}/stream`;
      const res = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${info.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ text }),
      });
      if (!res.ok || !res.body) {
        throw new Error(`${res.status} ${res.statusText}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let accumulated = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const eventMatch = frame.match(/^event:\s*(.+)$/m);
          const dataMatch = frame.match(/^data:\s*(.+)$/m);
          if (!eventMatch || !dataMatch) continue;
          const eventType = eventMatch[1].trim();
          const payload = JSON.parse(dataMatch[1]);
          if (eventType === "text_delta") {
            const chunk = payload.text ?? "";
            accumulated += chunk;
            setThinkingText(null);
            setStreamChunks((prev) => [...prev, chunk]);
            scrollToBottom();
          } else if (eventType === "assistant") {
            const finalText = payload.text ?? "";
            setThinkingText(null);
            setStreamChunks(finalText ? [finalText] : accumulated ? [accumulated] : []);
            scrollToBottom();
          } else if (eventType === "thinking_delta") {
            setThinkingText(payload.text ?? "Preparing coach context…");
          } else if (eventType === "done") {
            setThinkingText(null);
          } else if (eventType === "error") {
            const message = payload.error ?? "Coach stream failed.";
            setStreamError(message);
            throw new Error(message);
          }
        }
      }
    },
    onMutate: async ({ conversationId, text }) => {
      const queryKey = ["agent-messages", conversationId] as const;
      setStreamChunks([]);
      setThinkingText("Preparing coach context…");
      setStreamError(null);
      setInputText("");
      if (textareaRef.current) textareaRef.current.style.height = "auto";
      await qc.cancelQueries({ queryKey });
      const optimisticMessage: Message = {
        id: -Date.now(),
        role: "user",
        content: text,
        tool_calls_json: null,
        tool_results_json: null,
        evidence_citations_json: null,
        model: null,
        input_tokens: null,
        output_tokens: null,
      };
      qc.setQueryData<{ conversation_id: number; messages: Message[] }>(
        queryKey,
        (current) => ({
          conversation_id: conversationId,
          messages: [...(current?.messages ?? []), optimisticMessage],
        }),
      );
      scrollToBottom(true);
    },
    onSuccess: (_data, variables) => {
      setStreamChunks([]);
      setThinkingText(null);
      qc.invalidateQueries({ queryKey: ["agent-messages", variables.conversationId] });
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
    onError: (_error, variables) => {
      setThinkingText(null);
      qc.invalidateQueries({ queryKey: ["agent-messages", variables.conversationId] });
    },
  });

  const handleSend = () => {
    const trimmed = inputText.trim();
    if (!trimmed || !activeConvoId) return;
    sendMessage.mutate({ conversationId: activeConvoId, text: trimmed });
  };

  const handleInputChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
    e.currentTarget.style.height = "auto";
    e.currentTarget.style.height = `${Math.min(e.currentTarget.scrollHeight, 128)}px`;
  };

  const messages = messagesData?.messages ?? [];
  const visibleMessages = messages.filter((m) => m.role !== "tool_result");
  const hasStreamContent = streamChunks.length > 0 || thinkingText != null;
  const disableActions = sendMessage.isPending || deleteConvo.isPending;
  const errorText =
    streamError ??
    (sendMessage.error
      ? String((sendMessage.error as Error).message ?? sendMessage.error)
      : null) ??
    (deleteConvo.error
      ? String((deleteConvo.error as Error).message ?? deleteConvo.error)
      : null);

  return (
    <div className="agent-chat-wrapper">
      <div className="agent-chat__topbar">
        <button
          className="agent-chat__sidebar-toggle"
          onClick={() => setSidebarOpen(!sidebarOpen)}
          aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
        >
          {sidebarOpen ? "◀" : "▶"}
        </button>
        <span className="agent-chat__topbar-title">Coach</span>
        <span className="agent-chat__topbar-sub">
          Citation-grounded advice &middot; read-only guidance &middot; no drafting tools
        </span>
      </div>

      <div
        className={`agent-chat${sidebarOpen ? "" : " agent-chat--sidebar-collapsed"}`}
      >
        <aside className="agent-chat__sidebar">
          <div className="agent-chat__sidebar-header">
            <button
              className="agent-chat__new-btn"
              onClick={() => createConvo.mutate()}
              disabled={createConvo.isPending}
            >
              + new coach session
            </button>
          </div>

          <div className="agent-chat__convo-list">
            {coachConvos.map((c) => {
              const isConfirming = deleteConfirmId === c.id;
              const isDeleting = deleteConvo.isPending && deleteConvo.variables === c.id;
              return (
                <div key={c.id} className="agent-chat__convo-row">
                  <div
                    className={`agent-chat__convo-item${c.id === activeConvoId ? " agent-chat__convo-item--active" : ""}`}
                    onClick={() => {
                      if (!isConfirming) setActiveConvoId(c.id);
                    }}
                  >
                    <div className="agent-chat__convo-meta">
                      <span className="agent-chat__convo-kind">coach</span>
                      <span className="agent-chat__convo-time">
                        {c.created_at ? relativeTime(c.created_at) : "now"}
                      </span>
                    </div>
                    <div className="agent-chat__convo-title">
                      {c.title || `Coach #${c.id}`}
                    </div>
                  </div>

                  <div
                    className={`agent-chat__convo-actions${isConfirming ? " agent-chat__convo-actions--visible" : ""}`}
                  >
                    {isConfirming ? (
                      <>
                        <button
                          className="agent-chat__confirm-btn"
                          onClick={() => deleteConvo.mutate(c.id)}
                          disabled={disableActions}
                        >
                          {isDeleting ? "Deleting" : "Confirm"}
                        </button>
                        <button
                          className="agent-chat__keep-btn"
                          onClick={() => setDeleteConfirmId(null)}
                          disabled={deleteConvo.isPending}
                        >
                          Keep
                        </button>
                      </>
                    ) : (
                      <button
                        className="agent-chat__delete-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteConfirmId(c.id);
                        }}
                        disabled={disableActions}
                        aria-label={`Delete coach session ${c.id}`}
                      >
                        ×
                      </button>
                    )}
                  </div>
                </div>
              );
            })}

            {coachConvos.length === 0 && (
              <div className="agent-chat__sidebar-empty">
                No coach sessions yet.
              </div>
            )}
          </div>

          <div className="agent-chat__sidebar-footer">
            {coachConvos.length} coach session
            {coachConvos.length !== 1 ? "s" : ""}
          </div>
        </aside>

        <main className="agent-chat__thread">
          {activeConvoId == null ? (
            <div className="agent-chat__empty">
              <div className="agent-chat__empty-glyph">⌁</div>
              <div className="agent-chat__empty-title">Coach is evidence-first.</div>
              <div className="agent-chat__empty-sub">
                Start a Coach session for advice grounded in your dashboard data.
                Coach can analyze and redirect, but cannot draft posts or trigger write tools.
              </div>
              <div className="agent-chat__prompts">
                <button
                  className="agent-chat__prompt-chip"
                  onClick={() => createConvo.mutate()}
                  disabled={createConvo.isPending}
                >
                  open coach console
                </button>
              </div>
            </div>
          ) : (
            <>
              <div
                className="agent-chat__messages agent-chat__messages--coach"
                style={{ maxHeight: "55vh", marginBottom: "0.8rem", padding: 0 }}
              >
                {visibleMessages.map((m) => {
                  const tokenStr = formatTokens(m.input_tokens, m.output_tokens);
                  return (
                    <div key={m.id} className={`agent-msg agent-msg--${m.role}`}>
                      <div className="agent-msg__bubble">
                        <div className="agent-msg__header">
                          <span className="agent-msg__role">{m.role}</span>
                          {m.model && <span className="agent-msg__model">{m.model}</span>}
                          {m.role === "assistant" && !m.model && (
                            <span className="agent-msg__model">evidence filtered</span>
                          )}
                          {tokenStr && <span className="agent-msg__tokens">{tokenStr}</span>}
                        </div>
                        <div className="agent-msg__content">
                          {m.role === "assistant"
                            ? renderWithCitations(m.content ?? "")
                            : m.content ?? ""}
                        </div>
                      </div>
                    </div>
                  );
                })}

                {hasStreamContent && (
                  <div className="agent-msg agent-msg--streaming">
                    <div className="agent-msg__bubble">
                      <div className="agent-msg__header">
                        <span className="agent-msg__role">coach</span>
                        <span
                          className={`agent-msg__status agent-msg__status--${thinkingText ? "thinking" : "streaming"}`}
                        >
                          {thinkingText ? "reasoning" : "streaming"}
                        </span>
                      </div>
                      {thinkingText && (
                        <div className="agent-thinking">
                          <span className="agent-thinking__dots">
                            <span className="agent-thinking__dot" />
                            <span className="agent-thinking__dot" />
                            <span className="agent-thinking__dot" />
                          </span>
                          <span className="agent-thinking__label">{thinkingText}</span>
                        </div>
                      )}
                      {streamChunks.length > 0 && (
                        <div className="agent-msg__content">
                          {renderStreamingText(streamChunks)}
                          {sendMessage.isPending && <span className="agent-cursor" />}
                        </div>
                      )}
                    </div>
                  </div>
                )}
                {messages.length === 0 && !hasStreamContent && (
                  <p className="faint">
                    Ask the coach a question about your growth strategy.
                  </p>
                )}
                <div ref={scrollAnchorRef} />
              </div>

              <div className="agent-chat__input-bar">
                <div className="agent-chat__input-wrap">
                  <textarea
                    ref={textareaRef}
                    className="agent-chat__textarea"
                    value={inputText}
                    onChange={handleInputChange}
                    placeholder="Ask the coach…"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                      }
                    }}
                    disabled={sendMessage.isPending}
                    rows={1}
                  />
                </div>
                <button
                  className="agent-chat__send-btn"
                  onClick={handleSend}
                  disabled={sendMessage.isPending || !inputText.trim()}
                >
                  {sendMessage.isPending ? "···" : "Ask"}
                </button>
              </div>

              {errorText && (
                <div className="agent-chat__error">{errorText}</div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
};
