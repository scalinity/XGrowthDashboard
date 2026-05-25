/**
 * Brain Dump — faithful port of the §14.9 capture-first workflow.
 *
 * Layout: textarea at top for pasting raw thinking, submit button to agent,
 * below: SpecimenBlock for original paste, CandidateCard for generated drafts.
 * Shows recent brain-dump conversations and drafts from the agent.
 * No useEffect — useQuery + useMutation only.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { PrepublishChip } from "../components/badges";
import { CandidateCard, ConsoleLogRow, SpecimenBlock } from "../components/cards";
import { apiFetch } from "../lib/api";
import { palette, fonts } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface BrainDumpConversation {
  id: number;
  title: string | null;
  context_seed: string | null;
  created_at: string;
}

interface BrainDumpDraft {
  id: number;
  text: string;
  pillar: string | null;
  composite_label: string | null;
  status: string;
  similarity_warning_json: string | null;
}

interface BrainDumpData {
  conversations: BrainDumpConversation[];
  drafts: BrainDumpDraft[];
}

interface ProcessResponse {
  ok: boolean;
  brain_dump_id: number;
  candidates: unknown[];
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const BrainDumpView = () => {
  const qc = useQueryClient();
  const [rawText, setRawText] = useState("");
  const [submittedText, setSubmittedText] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["brain-dump"],
    queryFn: () => apiFetch<BrainDumpData>("/views/brain-dump"),
    retry: 1,
  });

  // Process a brain dump via the dedicated endpoint.
  const submitDump = useMutation({
    mutationFn: async (text: string) => {
      return apiFetch<ProcessResponse>("/brain-dumps", {
        method: "POST",
        body: JSON.stringify({ raw_text: text }),
      });
    },
    onSuccess: (resp, text) => {
      setSubmittedText(text);
      setRawText("");
      setSuccessMsg(`Processed — ${resp.candidates.length} candidates generated`);
      qc.invalidateQueries({ queryKey: ["brain-dump"] });
    },
    onError: () => {
      setSuccessMsg(null);
    },
  });

  const handleSubmit = () => {
    const trimmed = rawText.trim();
    if (!trimmed) return;
    submitDump.mutate(trimmed);
  };

  if (isLoading) return <p className="dim">Reading the local service...</p>;
  if (error) {
    return (
      <Callout>
        Couldn't reach the local service.{" "}
        <em>{String((error as Error).message ?? error)}</em>
      </Callout>
    );
  }

  const conversations = data?.conversations ?? [];
  const drafts = data?.drafts ?? [];

  return (
    <>
      <Kicker>BRAIN DUMP</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Brain Dump</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Capture-first workflow. Paste raw thinking, let the agent distill it
        into candidate drafts classified into your content lanes.
      </p>

      {/* Input area */}
      <div style={{ marginTop: "1rem" }}>
        <textarea
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          placeholder="Paste your raw thinking, notes, idea fragment, or observation here..."
          style={{
            width: "100%",
            minHeight: "8rem",
            resize: "vertical",
            background: palette.surfaceRaised,
            border: `1px solid ${palette.hairline}`,
            borderRadius: "2px",
            padding: "0.7rem",
            color: palette.bone,
            fontFamily: fonts.body,
            fontSize: "0.92rem",
            lineHeight: 1.5,
          }}
        />
        <button
          onClick={handleSubmit}
          disabled={submitDump.isPending || !rawText.trim()}
          style={{
            marginTop: "0.5rem",
            padding: "0.5rem 1.4rem",
            background: palette.phosphor,
            color: palette.ink,
            border: "none",
            borderRadius: "2px",
            fontFamily: fonts.body,
            fontWeight: 600,
            cursor: "pointer",
            opacity: submitDump.isPending ? 0.5 : 1,
          }}
        >
          {submitDump.isPending ? "Processing..." : "Process brain dump"}
        </button>
        {submitDump.error && (
          <p style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.3rem" }}>
            {String((submitDump.error as Error).message ?? submitDump.error)}
          </p>
        )}
        {successMsg && !submitDump.isPending && (
          <p style={{ color: palette.phosphor, fontSize: "0.82rem", marginTop: "0.3rem" }}>
            {successMsg}
          </p>
        )}
      </div>

      {/* Submitted specimen */}
      {submittedText && (
        <>
          <Hairline />
          <Kicker>ORIGINAL SPECIMEN</Kicker>
          <SpecimenBlock text={submittedText} />
        </>
      )}

      <Hairline />

      {/* Recent drafts from brain dumps */}
      <h2>Recent brain-dump drafts</h2>
      {drafts.length === 0 ? (
        <p className="faint">
          No brain-dump drafts yet. Submit raw thinking above and the agent
          will generate candidates.
        </p>
      ) : (
        drafts.map((d, i) => (
          <div key={d.id}>
            <CandidateCard
              index={i + 1}
              text={d.text}
              pillar={d.pillar ?? "—"}
              audience="—"
              cta="—"
              contentType="brain_dump"
              rationale=""
              statusLabel={d.status}
            />
            {d.composite_label && <PrepublishChip label={d.composite_label} />}
          </div>
        ))
      )}

      <Hairline />

      {/* Recent conversations */}
      <h2>Past brain-dump sessions</h2>
      {conversations.length === 0 ? (
        <p className="faint">No past brain-dump sessions.</p>
      ) : (
        conversations.map((c) => (
          <ConsoleLogRow
            key={c.id}
            timestamp={c.created_at?.slice(0, 16) ?? ""}
            kind="brain_dump"
            title={c.title || `Session #${c.id}`}
          />
        ))
      )}
    </>
  );
};
