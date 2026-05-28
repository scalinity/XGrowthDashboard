/**
 * Agent Chat — faithful port of app/pages/9_Agent_Chat.py (spec §14.8).
 *
 * Full-screen layout with collapsible sidebar, modern chat bubbles,
 * per-chunk fade-in streaming, animated thinking indicator.
 *
 * No useEffect — useMutation for sends, useQuery for reads, imperative
 * scroll via refs in event handlers and mutation callbacks.
 */
import { useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, userSafeApiError, waitForSidecar, apiBaseUrl } from "../lib/api";
import type { AgentModePayload } from "../lib/contracts";
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
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  confidence_label: string | null;
}

interface MessagesResponse {
  conversation_id: number;
  messages: Message[];
}

interface SendMessageVariables {
  conversationId: number;
  text: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const SUGGESTED_PROMPTS = [
  "Analyze my growth this week",
  "Draft 3 post ideas",
  "Review my engagement trends",
  "Find reply opportunities",
];

const VISIBLE_CHUNK_WINDOW = 60;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
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

function formatTokens(
  input: number | null,
  output: number | null,
): string | null {
  if (!input && !output) return null;
  const parts: string[] = [];
  if (input) parts.push(`${input.toLocaleString()}↓`);
  if (output) parts.push(`${output.toLocaleString()}↑`);
  return parts.join(" ");
}

// -- Markdown-lite rendering ------------------------------------------------

function renderContent(text: string): ReactNode {
  if (!text) return null;

  const result: ReactNode[] = [];
  const codeBlockRe = /```(\w*)\n?([\s\S]*?)```/g;
  let last = 0;
  let m: RegExpExecArray | null;

  while ((m = codeBlockRe.exec(text)) !== null) {
    if (m.index > last) {
      result.push(...renderSegment(text.slice(last, m.index), result.length));
    }
    result.push(
      <pre key={`cb${result.length}`} className="agent-code-block">
        {m[1] && <span className="agent-code-lang">{m[1]}</span>}
        <code>{m[2].trimEnd()}</code>
      </pre>,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    result.push(...renderSegment(text.slice(last), result.length));
  }
  return <>{result}</>;
}

function renderSegment(text: string, baseKey: number): ReactNode[] {
  const parts = text.split(/(\[tool: [^\]]+\])/g);
  return parts
    .map((part, i) => {
      const toolMatch = part.match(/^\[tool: ([^\]]+)\]$/);
      if (toolMatch) {
        return (
          <span key={`${baseKey}-t${i}`} className="agent-tool-badge">
            <span className="agent-tool-badge__icon">{"⚙"}</span>
            {toolMatch[1]}
          </span>
        );
      }
      if (!part) return null;
      return <span key={`${baseKey}-${i}`}>{renderInline(part)}</span>;
    })
    .filter(Boolean) as ReactNode[];
}

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const codeParts = text.split(/(`[^`\n]+`)/g);
  for (let i = 0; i < codeParts.length; i++) {
    const cp = codeParts[i];
    if (cp.startsWith("`") && cp.endsWith("`") && cp.length > 2) {
      nodes.push(<code key={`c${i}`}>{cp.slice(1, -1)}</code>);
    } else {
      const bolds = cp.split(/(\*\*[^*]+\*\*)/g);
      for (let j = 0; j < bolds.length; j++) {
        const b = bolds[j];
        if (b.startsWith("**") && b.endsWith("**") && b.length > 4) {
          nodes.push(<strong key={`b${i}.${j}`}>{b.slice(2, -2)}</strong>);
        } else if (b) {
          nodes.push(b);
        }
      }
    }
  }
  return nodes;
}

type ToolCallView = {
  id?: string;
  name?: string;
  tool_name?: string;
  input?: unknown;
};

type ToolResultView = {
  tool_call_id?: string | null;
  content?: string | null;
};

type LiveToolCall = {
  id: string;
  name: string;
  input?: unknown;
  inputSnapshot?: string;
  status: string;
  result?: unknown;
  error?: string | null;
  rationale?: string | null;
};

function formatToolPayload(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value ?? {}, null, 2);
}

function parseToolPayload(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function isEmptyToolInput(value: unknown, snapshot?: string): boolean {
  const payload = snapshot != null ? parseToolPayload(snapshot) : value;
  if (payload == null) return true;
  return (
    typeof payload === "object" &&
    !Array.isArray(payload) &&
    Object.keys(payload).length === 0
  );
}

function formatToolInputPayload(value: unknown, snapshot?: string): string {
  if (isEmptyToolInput(value, snapshot)) {
    return "No explicit input; exact payload is {}. Tool defaults are being used.";
  }
  return snapshot ?? formatToolPayload(value ?? {});
}

function toolInputClassName(value: unknown, snapshot?: string): string {
  return isEmptyToolInput(value, snapshot)
    ? "agent-tool-json agent-tool-json--empty-input"
    : "agent-tool-json";
}

function summarizeToolResult(toolName: string, value: unknown): string | null {
  const payload = parseToolPayload(value);
  if (payload == null || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  const record = payload as Record<string, unknown>;
  const status = typeof record.status === "string" ? record.status : null;

  if (toolName === "fetch_x_post") {
    if (status === "success") {
      const text = String(record.target_post_text ?? "").trim();
      const handle = String(record.target_author_handle ?? "").trim();
      const excerpt = text.length > 96 ? `${text.slice(0, 95)}…` : text;
      if (handle && excerpt) return `@${handle}: ${excerpt}`;
      if (excerpt) return excerpt;
      return "post fetched";
    }
    if (status === "refused") {
      return String(record.reason ?? record.error ?? "fetch refused");
    }
    if (status === "error") {
      const reason = record.reason ? `${record.reason}: ` : "";
      return `${reason}${String(record.error ?? "fetch failed")}`;
    }
  }

  if (toolName === "query_x_api") {
    if (status === "success") {
      return String(record.endpoint ?? "X API read complete");
    }
    if (status === "refused") {
      return String(record.reason ?? record.error ?? "X API read refused");
    }
    return String(record.error ?? "X API read failed");
  }

  if (toolName === "run_local_bash") {
    if (status === "success") {
      const stdout = String(record.stdout ?? "").trim();
      const firstLine = stdout.split("\n").find((line) => line.trim()) ?? "";
      return firstLine ? `exit 0 · ${firstLine}` : "exit 0";
    }
    if (status === "refused" || status === "timeout" || status === "error") {
      return String(record.error ?? record.reason ?? status);
    }
  }

  if (status === "refused") {
    return String(record.reason ?? record.error ?? "refused");
  }
  if (status === "degraded") {
    return String(record.manual_fallback ?? record.error ?? "degraded");
  }
  if (status === "error" || status === "failed") {
    return String(record.error ?? status);
  }

  return null;
}

function toolResultsByCallId(json?: string | null): Map<string, string> {
  const results = new Map<string, string>();
  if (!json) return results;
  try {
    const parsed = JSON.parse(json) as ToolResultView[];
    if (!Array.isArray(parsed)) return results;
    for (const item of parsed) {
      if (item.tool_call_id && item.content != null) {
        results.set(item.tool_call_id, item.content);
      }
    }
  } catch {
    return results;
  }
  return results;
}

function renderToolCalls(json: string, resultJson?: string | null): ReactNode {
  try {
    const calls = JSON.parse(json) as ToolCallView[];
    if (Array.isArray(calls)) {
      const results = toolResultsByCallId(resultJson);
      return (
        <div className="agent-tool-stack">
          {calls.map((c, i) => {
            const toolName = c.name ?? c.tool_name ?? "tool";
            const result = c.id ? results.get(c.id) : null;
            const summary = result ? summarizeToolResult(toolName, result) : null;
            return (
              <details key={`${toolName}-${c.id ?? i}`} className="agent-tool-detail">
                <summary className="agent-tool-detail__summary">
                  <span className="agent-tool-badge">
                    <span className="agent-tool-badge__icon">{"⚙"}</span>
                    {toolName}
                  </span>
                  <span className="agent-tool-detail__state">
                    {summary ?? (result ? "result ready" : "called")}
                  </span>
                </summary>
                <div className="agent-tool-detail__body">
                  <div className="agent-tool-detail__label">input</div>
                  <pre className={toolInputClassName(c.input)}>
                    {formatToolInputPayload(c.input)}
                  </pre>
                  {result && (
                    <>
                      <div className="agent-tool-detail__label">output</div>
                      <pre className="agent-tool-json">{formatToolPayload(result)}</pre>
                    </>
                  )}
                </div>
              </details>
            );
          })}
        </div>
      );
    }
  } catch {
    /* fallback below */
  }
  return <div className="agent-tool-json">{json}</div>;
}

function renderLiveToolCalls(calls: LiveToolCall[]): ReactNode {
  if (calls.length === 0) return null;
  return (
    <div className="agent-tool-stack agent-tool-stack--live">
      {calls.map((c) => {
        const state = c.error ? "error" : c.status || "running";
        const output = c.error ?? c.rationale ?? c.result;
        const summary =
          summarizeToolResult(c.name, output) ??
          (c.error ? String(c.error) : state.replaceAll("_", " "));
        return (
          <details key={c.id} className="agent-tool-detail agent-tool-detail--live" open>
            <summary className="agent-tool-detail__summary">
              <span className="agent-tool-badge">
                <span className="agent-tool-badge__icon">{"⚙"}</span>
                {c.name}
              </span>
              <span className={`agent-tool-detail__state agent-tool-detail__state--${state}`}>
                {summary}
              </span>
            </summary>
            <div className="agent-tool-detail__body">
              <div className="agent-tool-detail__label">input</div>
              <pre className={toolInputClassName(c.input, c.inputSnapshot)}>
                {formatToolInputPayload(c.input, c.inputSnapshot)}
              </pre>
              {output != null && (
                <>
                  <div className="agent-tool-detail__label">output</div>
                  <pre className="agent-tool-json">{formatToolPayload(output)}</pre>
                </>
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function mergeLiveToolCall(
  current: LiveToolCall[],
  payload: Record<string, unknown>,
): LiveToolCall[] {
  const name = String(payload.name ?? payload.tool_name ?? "tool");
  const id = String(payload.id ?? `${name}-${current.length}`);
  const next: LiveToolCall = {
    id,
    name,
    input: payload.input,
    status: String(payload.status ?? "running"),
  };
  const index = current.findIndex((item) => item.id === id);
  if (index === -1) return [...current, next];
  return current.map((item, itemIndex) =>
    itemIndex === index
      ? {
          ...item,
          ...next,
          input: payload.input ?? item.input,
          inputSnapshot: item.inputSnapshot,
          result: item.result,
          error: item.error,
          rationale: item.rationale,
        }
      : item,
  );
}

function mergeLiveToolInput(
  current: LiveToolCall[],
  snapshot: string,
): LiveToolCall[] {
  if (current.length === 0) return current;
  const lastIndex = current.length - 1;
  return current.map((item, itemIndex) =>
    itemIndex === lastIndex
      ? { ...item, inputSnapshot: snapshot, status: "forming" }
      : item,
  );
}

function mergeLiveToolResult(
  current: LiveToolCall[],
  payload: Record<string, unknown>,
): LiveToolCall[] {
  const name = String(payload.name ?? payload.tool_name ?? "tool");
  const id = String(payload.id ?? `${name}-${Math.max(0, current.length - 1)}`);
  const index = current.findIndex((item) => item.id === id);
  const result = {
    result: payload.result,
    error: (payload.error as string | null | undefined) ?? null,
    rationale: (payload.rationale as string | null | undefined) ?? null,
    status: String(payload.status ?? "result_ready"),
  };
  if (index === -1) {
    return [...current, { id, name, ...result }];
  }
  return current.map((item, itemIndex) =>
    itemIndex === index ? { ...item, ...result } : item,
  );
}

// -- Stream chunk rendering with fade-in ------------------------------------

function StreamChunks({
  chunks,
  showCursor,
}: {
  chunks: string[];
  showCursor: boolean;
}) {
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
    <div className="agent-msg__content">
      {historicalText && <span>{historicalText}</span>}
      {recentChunks.map((chunk, localIdx) => {
        const key = offset + localIdx;
        const toolMatch = chunk.match(/^\n?\[tool: ([^\]]+)\]\n?$/);
        if (toolMatch) {
          return (
            <span key={key} className="agent-tool-badge agent-stream-chunk">
              <span className="agent-tool-badge__icon">{"⚙"}</span>
              {toolMatch[1]}
            </span>
          );
        }
        return (
          <span key={key} className="agent-stream-chunk">
            {chunk}
          </span>
        );
      })}
      {showCursor && <span className="agent-cursor" />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const AgentChatView = () => {
  const qc = useQueryClient();
  const [activeConvoId, setActiveConvoId] = useState<number | null>(null);
  const [inputText, setInputText] = useState("");
  const [streamChunks, setStreamChunks] = useState<string[]>([]);
  const [thinkingText, setThinkingText] = useState<string | null>(null);
  const [liveToolCalls, setLiveToolCalls] = useState<LiveToolCall[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const scrollAnchorRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = (smooth = false) => {
    requestAnimationFrame(() => {
      scrollAnchorRef.current?.scrollIntoView({
        behavior: smooth ? "smooth" : "instant",
        block: "end",
      });
    });
  };

  // -- Queries --------------------------------------------------------------

  const { data: convos } = useQuery({
    queryKey: ["agent-conversations"],
    queryFn: () =>
      apiFetch<{ conversations: Conversation[] }>("/agent/conversations"),
    retry: 1,
  });

  const { data: agentMode } = useQuery({
    queryKey: ["agent-mode"],
    queryFn: () => apiFetch<AgentModePayload>("/agent/mode"),
    retry: 1,
  });

  const { data: messagesData } = useQuery({
    queryKey: ["agent-messages", activeConvoId],
    queryFn: () =>
      apiFetch<MessagesResponse>(
        `/agent/conversations/${activeConvoId}/messages`,
      ),
    enabled: activeConvoId != null,
    retry: 1,
  });

  // -- Mutations ------------------------------------------------------------

  const createConvo = useMutation({
    mutationFn: (params: { title?: string; context_seed?: string }) =>
      apiFetch<{ conversation_id: number }>("/agent/conversations", {
        method: "POST",
        body: JSON.stringify(params),
      }),
    onSuccess: (data) => {
      setActiveConvoId(data.conversation_id);
      setDeleteConfirmId(null);
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
        setLiveToolCalls([]);
        setStreamError(null);
      }
      setDeleteConfirmId(null);
      qc.removeQueries({ queryKey: ["agent-messages", conversationId] });
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
  });

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
      if (!res.ok) {
        let detail = `${res.status} ${res.statusText}`;
        try {
          const bodyText = await res.text();
          if (bodyText.trim()) detail = bodyText.trim();
        } catch {
          /* keep status summary */
        }
        throw new Error(userSafeApiError(new Error(detail)));
      }
      if (!res.body) throw new Error("Agent stream returned no body");

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
            if (finalText && !accumulated.endsWith(finalText)) {
              accumulated = accumulated ? `${accumulated}\n\n${finalText}` : finalText;
            }
            setThinkingText(null);
            setStreamChunks(accumulated ? [accumulated] : []);
            scrollToBottom();
          } else if (eventType === "user") {
            scrollToBottom();
          } else if (eventType === "thinking_delta") {
            setThinkingText(payload.text ?? "Thinking…");
          } else if (eventType === "done") {
            setThinkingText(null);
          } else if (eventType === "error") {
            const message = userSafeApiError(new Error(String(payload.error ?? "Agent stream failed.")));
            setStreamError(message);
            throw new Error(message);
          } else if (eventType === "tool_call") {
            const toolName = payload.name ?? payload.tool_name ?? "tool";
            setThinkingText(`Running ${toolName}…`);
            setLiveToolCalls((prev) =>
              mergeLiveToolCall(prev, payload as Record<string, unknown>),
            );
            scrollToBottom();
          } else if (eventType === "tool_input_delta") {
            setLiveToolCalls((prev) =>
              mergeLiveToolInput(prev, String(payload.snapshot ?? "")),
            );
            scrollToBottom();
          } else if (eventType === "tool_result") {
            setThinkingText(null);
            setLiveToolCalls((prev) =>
              mergeLiveToolResult(prev, payload as Record<string, unknown>),
            );
            scrollToBottom();
          }
        }
      }
      return accumulated;
    },
    onMutate: async ({ conversationId, text }) => {
      const queryKey = ["agent-messages", conversationId] as const;
      setStreamChunks([]);
      setThinkingText("Preparing agent context…");
      setLiveToolCalls([]);
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
        model: null,
        input_tokens: null,
        output_tokens: null,
        confidence_label: null,
      };
      qc.setQueryData<MessagesResponse>(queryKey, (current) => ({
        conversation_id: conversationId,
        messages: [...(current?.messages ?? []), optimisticMessage],
      }));
      scrollToBottom();
    },
    onSuccess: (_data, variables) => {
      setStreamChunks([]);
      setThinkingText(null);
      setLiveToolCalls([]);
      qc.invalidateQueries({ queryKey: ["agent-messages", variables.conversationId] });
      scrollToBottom(true);
    },
    onError: (_error, variables) => {
      setStreamChunks([]);
      setThinkingText(null);
      setLiveToolCalls([]);
      qc.invalidateQueries({ queryKey: ["agent-messages", variables.conversationId] });
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
  });

  // -- Handlers -------------------------------------------------------------

  const handleSend = () => {
    const trimmed = inputText.trim();
    if (!trimmed || !activeConvoId) return;
    sendMessage.mutate({ conversationId: activeConvoId, text: trimmed });
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
    const ta = e.target;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePromptClick = (prompt: string) => {
    setInputText(prompt);
    textareaRef.current?.focus();
  };

  // -- Derived state --------------------------------------------------------

  const conversations = convos?.conversations ?? [];
  const messages = messagesData?.messages ?? [];
  const visibleMessages = messages.filter((m) => m.role !== "tool_result");
  const hasStreamContent =
    streamChunks.length > 0 || thinkingText != null || liveToolCalls.length > 0;
  const disableActions = sendMessage.isPending || deleteConvo.isPending;
  const errorText =
    streamError ??
    (sendMessage.error ? userSafeApiError(sendMessage.error) : null);

  // -- Render ---------------------------------------------------------------

  return (
    <div className="agent-chat-wrapper">
      {/* ── Top bar ── */}
      <div className="agent-chat__topbar">
        <button
          className="agent-chat__sidebar-toggle"
          onClick={() => setSidebarOpen(!sidebarOpen)}
          aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
        >
          {sidebarOpen ? "◀" : "▶"}
        </button>
        <span className="agent-chat__topbar-title">Agent Chat</span>
        {agentMode && (
          <span
            className={`agent-chat__mode-chip${agentMode.niche_gate.blocked ? " agent-chat__mode-chip--warn" : ""}`}
            aria-label={`Agent mode ${agentMode.data_collection_mode}${agentMode.niche_gate.blocked ? ", niche gate blocking drafts" : ""}`}
          >
            {agentMode.data_collection_mode}
            {agentMode.niche_gate.blocked ? " · niche gate" : ""}
          </span>
        )}
        <span className="agent-chat__topbar-sub">
          Real-time reasoning &middot; tool use &middot; publish safeguards
        </span>
      </div>

      {/* ── Main container ── */}
      <div
        className={`agent-chat${sidebarOpen ? "" : " agent-chat--sidebar-collapsed"}`}
      >
        {/* ── Sidebar ── */}
        <aside className="agent-chat__sidebar">
          <div className="agent-chat__sidebar-header">
            <button
              className="agent-chat__new-btn"
              onClick={() =>
                createConvo.mutate({ title: "New conversation" })
              }
              disabled={createConvo.isPending}
            >
              + New Session
            </button>
          </div>

          <div className="agent-chat__convo-list">
            {conversations.map((c) => {
              const isConfirming = deleteConfirmId === c.id;
              const isDeleting =
                deleteConvo.isPending && deleteConvo.variables === c.id;
              return (
                <div key={c.id} className="agent-chat__convo-row">
                  <button
                    type="button"
                    className={`agent-chat__convo-item${c.id === activeConvoId ? " agent-chat__convo-item--active" : ""}`}
                    onClick={() => {
                      if (!isConfirming) setActiveConvoId(c.id);
                    }}
                  >
                    <div className="agent-chat__convo-meta">
                      <span className="agent-chat__convo-kind">
                        {c.context_seed ?? "chat"}
                      </span>
                      <span className="agent-chat__convo-time">
                        {relativeTime(c.created_at)}
                      </span>
                    </div>
                    <div className="agent-chat__convo-title">
                      {c.title || `Session #${c.id}`}
                    </div>
                  </button>

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
                      >
                        Delete
                      </button>
                    )}
                  </div>

                  {isConfirming && deleteConvo.error && (
                    <div className="agent-chat__delete-error">
                      {String(
                        (deleteConvo.error as Error).message ??
                          deleteConvo.error,
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {conversations.length === 0 && (
              <div
                style={{
                  padding: "1.5rem 0.7rem",
                  textAlign: "center",
                }}
              >
                <p
                  className="faint"
                  style={{
                    fontSize: "0.76rem",
                    lineHeight: 1.5,
                    margin: 0,
                  }}
                >
                  No sessions yet
                </p>
              </div>
            )}
          </div>

          {conversations.length > 0 && (
            <div className="agent-chat__sidebar-footer">
              {conversations.length} session
              {conversations.length !== 1 ? "s" : ""}
            </div>
          )}
        </aside>

        {/* ── Thread ── */}
        <div className="agent-chat__thread">
          {activeConvoId == null ? (
            <div className="agent-chat__empty">
              <div className="agent-chat__empty-glyph">{"◈"}</div>
              <div className="agent-chat__empty-title">Growth Agent</div>
              <div className="agent-chat__empty-sub">
                Select a session from the sidebar or start a new one to begin.
              </div>
            </div>
          ) : (
            <>
              {/* Messages */}
              <div className="agent-chat__messages">
                {visibleMessages.map((m) => {
                  const tokenStr = formatTokens(
                    m.input_tokens,
                    m.output_tokens,
                  );
                  return (
                    <div
                      key={m.id}
                      className={`agent-msg agent-msg--${m.role}`}
                    >
                      <div className="agent-msg__bubble">
                        <div className="agent-msg__header">
                          <span className="agent-msg__role">{m.role}</span>
                          {m.model && (
                            <span className="agent-msg__model">{m.model}</span>
                          )}
                          {tokenStr && (
                            <span className="agent-msg__tokens">
                              {tokenStr}
                            </span>
                          )}
                        </div>
                        <div className="agent-msg__content">
                          {m.role === "assistant"
                            ? renderContent(m.content ?? "")
                            : (m.content ?? "")}
                        </div>
                        {m.tool_calls_json &&
                          renderToolCalls(m.tool_calls_json, m.tool_results_json)}
                      </div>
                    </div>
                  );
                })}

                {/* Live streaming response */}
                {hasStreamContent && (
                  <div className="agent-msg agent-msg--streaming">
                    <div className="agent-msg__bubble">
                      <div className="agent-msg__header">
                        <span className="agent-msg__role">assistant</span>
                        <span
                          className={`agent-msg__status agent-msg__status--${thinkingText ? "thinking" : "streaming"}`}
                        >
                          {thinkingText ? "reasoning" : "streaming"}
                        </span>
                      </div>
                      {thinkingText && (
                        <div key={thinkingText} className="agent-thinking">
                          <div className="agent-thinking__dots">
                            <div className="agent-thinking__dot" />
                            <div className="agent-thinking__dot" />
                            <div className="agent-thinking__dot" />
                          </div>
                          <span className="agent-thinking__label">
                            {thinkingText}
                          </span>
                        </div>
                      )}
                      {streamChunks.length > 0 && (
                        <StreamChunks
                          chunks={streamChunks}
                          showCursor={sendMessage.isPending}
                        />
                      )}
                      {renderLiveToolCalls(liveToolCalls)}
                    </div>
                  </div>
                )}

                {/* Empty conversation — suggested prompts */}
                {visibleMessages.length === 0 && !hasStreamContent && (
                  <div className="agent-chat__empty">
                    <div className="agent-chat__empty-glyph">{"◈"}</div>
                    <div className="agent-chat__empty-title">
                      Start a conversation
                    </div>
                    <div className="agent-chat__empty-sub">
                      Ask the Growth Agent to analyze metrics, draft posts, or
                      strategize your next move.
                    </div>
                    <div className="agent-chat__prompts">
                      {SUGGESTED_PROMPTS.map((p) => (
                        <button
                          key={p}
                          className="agent-chat__prompt-chip"
                          onClick={() => handlePromptClick(p)}
                        >
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <div ref={scrollAnchorRef} style={{ height: 1 }} />
              </div>

              {/* Input bar */}
              <div className="agent-chat__input-bar">
                <div className="agent-chat__input-wrap">
                  <textarea
                    ref={textareaRef}
                    className="agent-chat__textarea"
                    value={inputText}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    placeholder="Message the Growth Agent…"
                    disabled={sendMessage.isPending}
                    rows={1}
                  />
                </div>
                <button
                  className="agent-chat__send-btn"
                  onClick={handleSend}
                  disabled={sendMessage.isPending || !inputText.trim()}
                >
                  {sendMessage.isPending ? "···" : "Send"}
                </button>
              </div>

              {/* Errors */}
              {errorText && (
                <div className="agent-chat__error">{errorText}</div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
