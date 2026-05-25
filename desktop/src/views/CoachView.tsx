/**
 * Coach — faithful port of the §14.10 advice-only, citation-grounded view.
 *
 * Similar to Agent Chat but with CitationChip discipline: parses
 * 〔record_type id_or_filter〕 patterns from assistant text and renders them
 * inline as CitationChip components.
 * No useEffect — useQuery + useMutation only.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { CitationChip } from "../components/badges";
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
}

// ---------------------------------------------------------------------------
// Citation parser — finds 〔record_type id_or_filter〕 in text
// ---------------------------------------------------------------------------
const CITATION_RE = /〔([^\s〕]+)\s+([^〕]+)〕/g;

function renderWithCitations(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
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

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const CoachView = () => {
  const qc = useQueryClient();
  const [activeConvoId, setActiveConvoId] = useState<number | null>(null);
  const [inputText, setInputText] = useState("");

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
      apiFetch<{ conversation_id: number; messages: Message[] }>(
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
      qc.invalidateQueries({ queryKey: ["agent-conversations"] });
    },
  });

  // Send message.
  const sendMessage = useMutation({
    mutationFn: (text: string) =>
      apiFetch<{ assistant_text: string | null; error: string | null }>(
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

  const messages = messagesData?.messages ?? [];

  return (
    <>
      <Kicker>COACH</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Coach</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Advice-only mode with citation-grounded responses. The coach cannot
        draft posts or trigger tools — only advise, grounded in your data.
        Citations appear as chips linking back to records.
      </p>

      <div style={{ display: "flex", gap: "1.2rem", marginTop: "1rem" }}>
        {/* Sidebar */}
        <div style={{ width: 240, flexShrink: 0 }}>
          <button
            onClick={() => createConvo.mutate()}
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
            + New coach session
          </button>
          <div style={{ maxHeight: "60vh", overflowY: "auto" }}>
            {coachConvos.map((c) => (
              <div
                key={c.id}
                onClick={() => setActiveConvoId(c.id)}
                style={{ cursor: "pointer" }}
              >
                <ConsoleLogRow
                  timestamp={c.created_at?.slice(0, 16) ?? ""}
                  kind="coach"
                  title={c.title || `Coach #${c.id}`}
                  active={c.id === activeConvoId}
                />
              </div>
            ))}
            {coachConvos.length === 0 && (
              <p className="faint" style={{ fontSize: "0.85rem" }}>
                No coach sessions yet.
              </p>
            )}
          </div>
        </div>

        {/* Thread */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {activeConvoId == null ? (
            <Callout>
              Select a coach session or start a new one.
            </Callout>
          ) : (
            <>
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
                      {m.role === "user" ? "YOU" : "COACH"}
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
                      {m.role === "assistant" && m.content
                        ? renderWithCitations(m.content)
                        : m.content ?? ""}
                    </div>
                  </div>
                ))}
                {messages.length === 0 && (
                  <p className="faint">
                    Ask the coach a question about your growth strategy.
                  </p>
                )}
              </div>

              <Hairline />
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
                <textarea
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  placeholder="Ask the coach..."
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
                  {sendMessage.isPending ? "..." : "Ask"}
                </button>
              </div>
              {sendMessage.error && (
                <p style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.3rem" }}>
                  {String((sendMessage.error as Error).message ?? sendMessage.error)}
                </p>
              )}
              {sendMessage.data?.error && (
                <p style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.3rem" }}>
                  {sendMessage.data.error}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
};
