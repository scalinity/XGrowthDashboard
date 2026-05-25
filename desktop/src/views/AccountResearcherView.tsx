/**
 * Account Researcher — faithful port of the §28.24 target account analysis.
 *
 * Layout: paste area for an X profile URL/handle, submit button to agent,
 * below: list of previously analyzed accounts with their analysis notes.
 * No useEffect — useQuery + useMutation only.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { StatusChip } from "../components/badges";
import { apiFetch } from "../lib/api";
import { palette, fonts } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface TargetAccount {
  x_handle: string;
  display_name: string | null;
  lane: string | null;
  priority: number | null;
  notes: string | null;
  is_active: boolean | number;
  last_engaged_at: string | null;
  created_at_utc: string;
}

interface AccountResearcherData {
  accounts: TargetAccount[];
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const AccountResearcherView = () => {
  const qc = useQueryClient();
  const [handleInput, setHandleInput] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["account-researcher"],
    queryFn: () => apiFetch<AccountResearcherData>("/views/account-researcher"),
    retry: 1,
  });

  // Submit a handle for analysis via agent conversation.
  const analyzeAccount = useMutation({
    mutationFn: async (handle: string) => {
      const { conversation_id } = await apiFetch<{ conversation_id: number }>(
        "/agent/conversations",
        {
          method: "POST",
          body: JSON.stringify({
            title: `Research: @${handle}`,
            context_seed: "account_research",
          }),
        },
      );
      const turn = await apiFetch<{ assistant_text: string | null; error: string | null }>(
        `/agent/conversations/${conversation_id}/messages`,
        {
          method: "POST",
          body: JSON.stringify({
            text: `Analyze this X account for reply targeting: @${handle}`,
          }),
        },
      );
      return { conversation_id, turn };
    },
    onSuccess: () => {
      setHandleInput("");
      qc.invalidateQueries({ queryKey: ["account-researcher"] });
    },
  });

  const handleSubmit = () => {
    const clean = handleInput.trim().replace(/^@/, "");
    if (!clean) return;
    analyzeAccount.mutate(clean);
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

  const accounts = data?.accounts ?? [];

  return (
    <>
      <Kicker>ACCOUNT RESEARCHER</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Account Researcher</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Analyze a target account into the reply queue. Paste an X handle and
        the agent will assess relevance, engagement surface, and niche fit.
      </p>

      {/* Input area */}
      <div style={{ marginTop: "1rem", display: "flex", gap: "0.5rem", alignItems: "flex-end" }}>
        <input
          type="text"
          value={handleInput}
          onChange={(e) => setHandleInput(e.target.value)}
          placeholder="@handle or profile URL"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleSubmit();
            }
          }}
          style={{
            flex: 1,
            maxWidth: 400,
            padding: "0.5rem 0.7rem",
            background: palette.surfaceRaised,
            border: `1px solid ${palette.hairline}`,
            borderRadius: "2px",
            color: palette.bone,
            fontFamily: fonts.body,
            fontSize: "0.9rem",
          }}
        />
        <button
          onClick={handleSubmit}
          disabled={analyzeAccount.isPending || !handleInput.trim()}
          style={{
            padding: "0.5rem 1.2rem",
            background: palette.phosphor,
            color: palette.ink,
            border: "none",
            borderRadius: "2px",
            fontFamily: fonts.body,
            fontWeight: 600,
            cursor: "pointer",
            opacity: analyzeAccount.isPending ? 0.5 : 1,
          }}
        >
          {analyzeAccount.isPending ? "Analyzing..." : "Analyze"}
        </button>
      </div>
      {analyzeAccount.error && (
        <p style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.3rem" }}>
          {String((analyzeAccount.error as Error).message ?? analyzeAccount.error)}
        </p>
      )}

      <Hairline />

      {/* Analyzed accounts list */}
      <h2>Analyzed accounts</h2>
      {accounts.length === 0 ? (
        <Callout>
          <em>No target accounts analyzed yet.</em> Paste a handle above to
          get started.
        </Callout>
      ) : (
        accounts.map((acct) => (
          <div
            key={acct.x_handle}
            style={{
              padding: "0.6rem 0.85rem",
              margin: "0.4rem 0",
              background: palette.surface,
              borderLeft: `2px solid ${acct.is_active ? palette.phosphor : palette.hairline}`,
              borderRadius: "2px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
              }}
            >
              <span style={{ color: palette.bone, fontWeight: 500 }}>
                @{acct.x_handle}
                {acct.display_name && (
                  <span
                    style={{
                      color: palette.boneDim,
                      fontWeight: 400,
                      marginLeft: "0.5rem",
                    }}
                  >
                    {acct.display_name}
                  </span>
                )}
              </span>
              <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                {acct.lane && (
                  <span className="numeric" style={{ fontSize: "0.75rem", color: palette.boneDim }}>
                    {acct.lane}
                  </span>
                )}
                <StatusChip
                  label={acct.is_active ? "active" : "inactive"}
                  tone={acct.is_active ? "active" : "neutral"}
                />
              </div>
            </div>
            {acct.notes && (
              <div
                style={{
                  marginTop: "0.3rem",
                  color: palette.boneDim,
                  fontSize: "0.85rem",
                  lineHeight: 1.4,
                }}
              >
                {acct.notes.length > 300
                  ? acct.notes.slice(0, 299) + "..."
                  : acct.notes}
              </div>
            )}
            <div
              className="numeric"
              style={{
                fontSize: "0.72rem",
                color: palette.boneFaint,
                marginTop: "0.2rem",
              }}
            >
              priority {acct.priority ?? "—"}
              {acct.last_engaged_at && ` · last engaged ${acct.last_engaged_at.slice(0, 10)}`}
              {" · added "}
              {acct.created_at_utc?.slice(0, 10) ?? "—"}
            </div>
          </div>
        ))
      )}
    </>
  );
};
