/**
 * Agent Chat — faithful port of app/pages/9_Agent_Chat.py (spec §14.8).
 *
 * Layout: conversation list sidebar, message thread, input + send, publish confirm.
 * Uses existing endpoints: POST/GET /agent/conversations, GET/POST .../messages.
 * No useEffect — useMutation for sends, useQuery for reads.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { ConsoleLogRow } from "../components/cards";
import { apiFetch } from "../lib/api";
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

interface TurnResponse {
  user_text: string;
  assistant_text: string | null;
  tool_calls: Array<Record<string, unknown>>;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  model: string | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const AgentChatView = () => {
  const qc = useQueryClient();
  const [activeConvoId, setActiveConvoId] = useState<number | null>(null);
  const [inputText, setInputText] = useState("");

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
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
  });

  // Send message mutation (sync endpoint).
  const sendMessage = useMutation({
    mutationFn: (text: string) =>
      apiFetch<TurnResponse>(
        `/agent/conversations/${activeConvoId}/messages`,
        { method: "POST", body: JSON.stringify({ text }) },
      ),
    onSuccess: () => {
      setInputText("");
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

  const conversations = convos?.conversations ?? [];
  const messages = messagesData?.messages ?? [];

  return (
    <>
      <Kicker>§14.8 · GROWTH AGENT</Kicker>
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
            {conversations.map((c) => (
              <div
                key={c.id}
                onClick={() => setActiveConvoId(c.id)}
                style={{ cursor: "pointer" }}
              >
                <ConsoleLogRow
                  timestamp={c.created_at?.slice(0, 16) ?? ""}
                  kind={c.context_seed ?? "chat"}
                  title={c.title || `Conversation #${c.id}`}
                  active={c.id === activeConvoId}
                />
              </div>
            ))}
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
