/**
 * Agent Chat — faithful port of app/pages/9_Agent_Chat.py (spec §14.8).
 *
 * Layout: conversation list sidebar, message thread, input + send, publish confirm.
 * Uses existing endpoints: POST/GET/DELETE /agent/conversations, GET/POST .../messages.
 * No useEffect — useMutation for sends, useQuery for reads.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { ConsoleLogRow } from "../components/cards";
import { apiFetch, waitForSidecar, apiBaseUrl } from "../lib/api";
import { palette, fonts } from "../theme/tokens";

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
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  confidence_label: string | null;
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const AgentChatView = () => {
  const qc = useQueryClient();
  const [activeConvoId, setActiveConvoId] = useState<number | null>(null);
  const [inputText, setInputText] = useState("");
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [thinkingText, setThinkingText] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);

  // List conversations.
  const { data: convos } = useQuery({
    queryKey: ["agent-conversations"],
    queryFn: () =>
      apiFetch<{ conversations: Conversation[] }>("/agent/conversations"),
    retry: 1,
  });

  // Messages for active conversation.
  const { data: messagesData } = useQuery({
    queryKey: ["agent-messages", activeConvoId],
    queryFn: () =>
      apiFetch<{ conversation_id: number; messages: Message[] }>(
        `/agent/conversations/${activeConvoId}/messages`,
      ),
    enabled: activeConvoId != null,
    retry: 1,
  });

  // Create conversation mutation.
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
        setStreamingText(null);
        setThinkingText(null);
        setStreamError(null);
      }
      setDeleteConfirmId(null);
      qc.removeQueries({ queryKey: ["agent-messages", conversationId] });
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
  });

  // Send message via SSE stream — tokens arrive in real time.
  const sendMessage = useMutation({
    mutationFn: async (text: string) => {
      setStreamingText("");
      setThinkingText("Preparing agent context...");
      setStreamError(null);
      const info = await waitForSidecar();
      const url = `${apiBaseUrl(info)}/agent/conversations/${activeConvoId}/stream`;
      const res = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${info.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ text }),
      });
      if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let accumulated = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // Parse SSE frames: "event: <type>\ndata: <json>\n\n"
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const eventMatch = frame.match(/^event:\s*(.+)$/m);
          const dataMatch = frame.match(/^data:\s*(.+)$/m);
          if (!eventMatch || !dataMatch) continue;
          const eventType = eventMatch[1].trim();
          const payload = JSON.parse(dataMatch[1]);
          if (eventType === "text_delta") {
            accumulated += payload.text ?? "";
            setThinkingText(null);
            setStreamingText(accumulated);
          } else if (eventType === "assistant") {
            accumulated = payload.text ?? "";
            setThinkingText(null);
            setStreamingText(accumulated);
          } else if (eventType === "thinking_delta") {
            setThinkingText(payload.text ?? "Thinking...");
          } else if (eventType === "done") {
            setThinkingText(null);
          } else if (eventType === "error") {
            const message = payload.error ?? "Agent stream failed.";
            setStreamError(message);
            throw new Error(message);
          } else if (eventType === "tool_call") {
            // Show tool call inline during streaming.
            const toolName = payload.name ?? payload.tool_name ?? "tool";
            accumulated += `\n[tool: ${toolName}]\n`;
            setThinkingText(null);
            setStreamingText(accumulated);
          }
        }
      }
      return accumulated;
    },
    onSuccess: () => {
      setInputText("");
      setStreamingText(null);
      setThinkingText(null);
      qc.invalidateQueries({ queryKey: ["agent-messages", activeConvoId] });
    },
    onError: () => {
      setStreamingText(null);
      setThinkingText(null);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["agent-messages", activeConvoId] });
    },
  });

  const handleSend = () => {
    const trimmed = inputText.trim();
    if (!trimmed || !activeConvoId) return;
    sendMessage.mutate(trimmed);
  };

  const handleNewConvo = () => {
    createConvo.mutate({ title: "New conversation" });
  };

  const handleDeleteConvo = (conversationId: number) => {
    deleteConvo.mutate(conversationId);
  };

  const conversations = convos?.conversations ?? [];
  const messages = messagesData?.messages ?? [];

  return (
    <>
      <Kicker>GROWTH AGENT</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Agent Chat</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Streaming chat with visible tool calls and publish confirmation.
        The agent cannot publish without your typed "confirm".
      </p>

      <div style={{ display: "flex", gap: "1.2rem", marginTop: "1rem" }}>
        {/* Conversation sidebar */}
        <div style={{ width: 280, flexShrink: 0 }}>
          <button
            onClick={handleNewConvo}
            disabled={createConvo.isPending}
            style={{
              width: "100%",
              padding: "0.5rem",
              marginBottom: "0.6rem",
              background: palette.phosphorDim,
              color: palette.bone,
              border: "none",
              borderRadius: "2px",
              fontFamily: fonts.body,
              cursor: "pointer",
            }}
          >
            + New conversation
          </button>
          <div style={{ maxHeight: "60vh", overflowY: "auto" }}>
            {conversations.map((c) => {
              const isConfirmingDelete = deleteConfirmId === c.id;
              const isDeleting = deleteConvo.isPending && deleteConvo.variables === c.id;
              const disableActions = sendMessage.isPending || deleteConvo.isPending;
              return (
                <div key={c.id} style={{ marginBottom: "0.35rem" }}>
                  <div
                    onClick={() => {
                      if (!isConfirmingDelete) setActiveConvoId(c.id);
                    }}
                    onKeyDown={(e) => {
                      if ((e.key === "Enter" || e.key === " ") && !isConfirmingDelete) {
                        e.preventDefault();
                        setActiveConvoId(c.id);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open ${c.title || `Conversation ${c.id}`}`}
                    style={{ cursor: isConfirmingDelete ? "default" : "pointer" }}
                  >
                    <ConsoleLogRow
                      timestamp={c.created_at?.slice(0, 16) ?? ""}
                      kind={c.context_seed ?? "chat"}
                      title={c.title || `Conversation #${c.id}`}
                      active={c.id === activeConvoId}
                    />
                  </div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "flex-end",
                      gap: "0.35rem",
                      margin: "0 0 0.25rem 0.6rem",
                    }}
                  >
                    {isConfirmingDelete ? (
                      <>
                        <button
                          type="button"
                          onClick={() => handleDeleteConvo(c.id)}
                          disabled={disableActions}
                          style={{
                            padding: "0.2rem 0.45rem",
                            background: palette.warnAmber,
                            color: palette.ink,
                            border: "none",
                            borderRadius: "2px",
                            fontFamily: fonts.mono,
                            fontSize: "0.62rem",
                            letterSpacing: "0.06em",
                            textTransform: "uppercase",
                            cursor: disableActions ? "not-allowed" : "pointer",
                            opacity: disableActions ? 0.55 : 1,
                          }}
                        >
                          {isDeleting ? "Deleting" : "Confirm"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteConfirmId(null)}
                          disabled={deleteConvo.isPending}
                          style={{
                            padding: "0.2rem 0.45rem",
                            background: palette.surfaceRaised,
                            color: palette.boneDim,
                            border: `1px solid ${palette.hairline}`,
                            borderRadius: "2px",
                            fontFamily: fonts.mono,
                            fontSize: "0.62rem",
                            letterSpacing: "0.06em",
                            textTransform: "uppercase",
                            cursor: deleteConvo.isPending ? "not-allowed" : "pointer",
                            opacity: deleteConvo.isPending ? 0.55 : 1,
                          }}
                        >
                          Keep
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteConfirmId(c.id);
                        }}
                        disabled={disableActions}
                        aria-label={`Delete ${c.title || `Conversation ${c.id}`}`}
                        style={{
                          padding: "0.16rem 0.45rem",
                          background: "transparent",
                          color: palette.boneFaint,
                          border: `1px solid ${palette.hairline}`,
                          borderRadius: "2px",
                          fontFamily: fonts.mono,
                          fontSize: "0.6rem",
                          letterSpacing: "0.06em",
                          textTransform: "uppercase",
                          cursor: disableActions ? "not-allowed" : "pointer",
                          opacity: disableActions ? 0.45 : 1,
                        }}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                  {isConfirmingDelete && deleteConvo.error && (
                    <p style={{ color: palette.warnAmber, fontSize: "0.72rem", margin: "0 0 0.35rem 0.6rem" }}>
                      {String((deleteConvo.error as Error).message ?? deleteConvo.error)}
                    </p>
                  )}
                </div>
              );
            })}
            {conversations.length === 0 && (
              <p className="faint" style={{ fontSize: "0.85rem" }}>
                No conversations yet. Start one above.
              </p>
            )}
          </div>
        </div>

        {/* Message thread + input */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {activeConvoId == null ? (
            <Callout>
              Select a conversation from the sidebar or start a new one.
            </Callout>
          ) : (
            <>
              {/* Messages */}
              <div
                style={{
                  maxHeight: "55vh",
                  overflowY: "auto",
                  marginBottom: "0.8rem",
                }}
              >
                {messages.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      padding: "0.5rem 0.7rem",
                      margin: "0.3rem 0",
                      background:
                        m.role === "user"
                          ? palette.surfaceRaised
                          : palette.surface,
                      borderLeft: `2px solid ${m.role === "user" ? palette.boneDim : palette.phosphor}`,
                      borderRadius: "2px",
                    }}
                  >
                    <div
                      className="kicker"
                      style={{
                        color:
                          m.role === "user"
                            ? palette.boneDim
                            : palette.phosphor,
                        marginBottom: "0.2rem",
                      }}
                    >
                      {m.role.toUpperCase()}
                      {m.model && (
                        <span
                          className="numeric"
                          style={{
                            fontSize: "0.68rem",
                            color: palette.boneFaint,
                            marginLeft: "0.5rem",
                          }}
                        >
                          {m.model}
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        color: palette.bone,
                        fontSize: "0.92rem",
                        lineHeight: 1.5,
                        whiteSpace: "pre-wrap",
                        wordWrap: "break-word",
                      }}
                    >
                      {m.content ?? ""}
                    </div>
                    {m.tool_calls_json && (
                      <div
                        style={{
                          marginTop: "0.4rem",
                          padding: "0.3rem 0.5rem",
                          background: palette.ink,
                          borderRadius: "2px",
                          fontSize: "0.78rem",
                          fontFamily: fonts.mono,
                          color: palette.boneDim,
                          maxHeight: "6rem",
                          overflowY: "auto",
                        }}
                      >
                        {m.tool_calls_json}
                      </div>
                    )}
                  </div>
                ))}
                {messages.length === 0 && (
                  <p className="faint">
                    No messages in this conversation yet.
                  </p>
                )}
                {/* Live streaming assistant response */}
                {(streamingText != null || thinkingText != null) && (
                  <div
                    style={{
                      padding: "0.5rem 0.7rem",
                      margin: "0.3rem 0",
                      background: palette.surface,
                      borderLeft: `2px solid ${palette.phosphor}`,
                      borderRadius: "2px",
                    }}
                  >
                    <div className="kicker" style={{ color: palette.phosphor, marginBottom: "0.2rem" }}>
                      ASSISTANT <span className="numeric" style={{ fontSize: "0.68rem", color: palette.boneFaint, marginLeft: "0.5rem" }}>{thinkingText ? "thinking..." : "streaming..."}</span>
                    </div>
                    {thinkingText && (
                      <div className="faint" style={{ fontSize: "0.82rem", lineHeight: 1.45, marginBottom: streamingText ? "0.35rem" : 0 }}>
                        {thinkingText}
                      </div>
                    )}
                    <div style={{ color: palette.bone, fontSize: "0.92rem", lineHeight: 1.5, whiteSpace: "pre-wrap", wordWrap: "break-word" }}>
                      {streamingText || (!thinkingText ? "..." : "")}
                    </div>
                  </div>
                )}
              </div>

              {/* Input */}
              <Hairline />
              <div
                style={{
                  display: "flex",
                  gap: "0.5rem",
                  marginTop: "0.5rem",
                }}
              >
                <textarea
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  placeholder="Type a message..."
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                  style={{
                    flex: 1,
                    minHeight: "3rem",
                    resize: "vertical",
                    background: palette.surfaceRaised,
                    border: `1px solid ${palette.hairline}`,
                    borderRadius: "2px",
                    padding: "0.5rem",
                    color: palette.bone,
                    fontFamily: fonts.body,
                    fontSize: "0.9rem",
                  }}
                />
                <button
                  onClick={handleSend}
                  disabled={sendMessage.isPending || !inputText.trim()}
                  style={{
                    padding: "0.5rem 1.2rem",
                    background: palette.phosphor,
                    color: palette.ink,
                    border: "none",
                    borderRadius: "2px",
                    fontFamily: fonts.body,
                    fontWeight: 600,
                    cursor: "pointer",
                    alignSelf: "flex-end",
                    opacity: sendMessage.isPending ? 0.5 : 1,
                  }}
                >
                  {sendMessage.isPending ? "..." : "Send"}
                </button>
              </div>
              {streamError && (
                <p style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.3rem" }}>
                  {streamError}
                </p>
              )}
              {sendMessage.error && (
                <p style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.3rem" }}>
                  {String((sendMessage.error as Error).message ?? sendMessage.error)}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
};
