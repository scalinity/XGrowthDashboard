/**
 * Manual Entry — faithful port of app/pages/8_Manual_Entry.py (spec §15).
 *
 * Tabbed hub for all data-entry forms. Uses existing POST endpoints:
 * /forms/snapshot, /forms/post, /forms/correction. Additional form tabs
 * (classify, daily reps, stir event, tester, queues) use placeholders
 * until their endpoints are added.
 *
 * No useEffect.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { apiFetch } from "../lib/api";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------
const TABS = [
  "Snapshot",
  "Correction",
  "Post / Reply",
  "Classify",
  "Daily reps",
  "Stir event",
  "Tester",
  "Needs tagging",
  "Needs post ID",
] as const;

type TabId = (typeof TABS)[number];

// ---------------------------------------------------------------------------
// Snapshot form (mirrors snapshot.render)
// ---------------------------------------------------------------------------
function SnapshotTab() {
  const queryClient = useQueryClient();
  const [followers, setFollowers] = useState("");
  const [following, setFollowing] = useState("");
  const [posts, setPosts] = useState("");
  const [listed, setListed] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ snapshot_id: number }>("/forms/snapshot", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["today"] });
      setFollowers(""); setFollowing(""); setPosts(""); setListed("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      snapshot_date: new Date().toISOString().slice(0, 10),
      username: "", // Will use server defaults
      profile_url: "",
      baseline_followers: 0,
      followers_count: parseInt(followers, 10),
      following_count: parseInt(following, 10),
      post_count: parseInt(posts, 10),
      listed_count: parseInt(listed, 10) || 0,
    });
  };

  const valid = followers !== "" && following !== "" && posts !== "" &&
    !isNaN(parseInt(followers)) && !isNaN(parseInt(following)) && !isNaN(parseInt(posts));

  return (
    <div>
      <h3>Daily snapshot</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        §15.1 — 30 seconds. Source='manual', data_quality='manual'.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: "0.5rem", marginBottom: "0.6rem" }}>
        {[
          { label: "Followers", value: followers, set: setFollowers },
          { label: "Following", value: following, set: setFollowing },
          { label: "Posts", value: posts, set: setPosts },
          { label: "Listed", value: listed, set: setListed },
        ].map(({ label, value, set }) => (
          <div key={label}>
            <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>{label}</label>
            <input type="number" value={value} onChange={(e) => set(e.target.value)} style={{ width: "100%" }} />
          </div>
        ))}
      </div>
      <button onClick={handleSubmit} disabled={!valid || mutation.isPending} className={valid ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Save daily snapshot"}
      </button>
      {mutation.isSuccess && <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>Snapshot saved.</p>}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Post / Reply form (mirrors post_log.render)
// ---------------------------------------------------------------------------
function PostReplyTab() {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [postType, setPostType] = useState("post");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ post_id: number }>("/forms/post", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["today"] });
      setText("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      text,
      type: postType,
      created_date: new Date().toISOString().slice(0, 10),
    });
  };

  return (
    <div>
      <h3>Log a post or reply</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        §15.3 — paste the text you published. Classification happens in the Classify tab.
      </p>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Type</label>
        <select value={postType} onChange={(e) => setPostType(e.target.value)}>
          <option value="post">standalone</option>
          <option value="reply">reply</option>
          <option value="quote">quote</option>
        </select>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Text</label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={5}
          style={{ width: "100%", resize: "vertical" }}
          placeholder="Paste the exact text you posted or replied…"
        />
      </div>
      <div className="numeric" style={{ fontSize: "0.82rem", color: palette.boneDim, marginBottom: "0.5rem" }}>
        {text.length} / 280 characters
      </div>
      <button onClick={handleSubmit} disabled={!text.trim() || mutation.isPending} className={text.trim() ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Log post"}
      </button>
      {mutation.isSuccess && <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>Post logged.</p>}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Correction tab
// ---------------------------------------------------------------------------
function CorrectionTab() {
  const [snapshotId, setSnapshotId] = useState("");
  const [field, setField] = useState("");
  const [oldValue, setOldValue] = useState("");
  const [newValue, setNewValue] = useState("");
  const [reason, setReason] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ correction_id: number }>("/forms/correction", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setSnapshotId(""); setField(""); setOldValue(""); setNewValue(""); setReason("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      snapshot_id: parseInt(snapshotId, 10),
      field_name: field,
      old_value: oldValue || null,
      new_value: newValue,
      reason,
    });
  };

  const valid = snapshotId && field && newValue && reason;

  return (
    <div>
      <h3>Record a correction</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        §13 hard rule 2 — snapshots are immutable. Corrections are additive (original preserved).
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Snapshot ID</label>
          <input type="number" value={snapshotId} onChange={(e) => setSnapshotId(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Field name</label>
          <input value={field} onChange={(e) => setField(e.target.value)} style={{ width: "100%" }} placeholder="e.g. followers_count" />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Old value</label>
          <input value={oldValue} onChange={(e) => setOldValue(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>New value</label>
          <input value={newValue} onChange={(e) => setNewValue(e.target.value)} style={{ width: "100%" }} />
        </div>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Reason</label>
        <input value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: "100%" }} placeholder="Why the correction is needed" />
      </div>
      <button onClick={handleSubmit} disabled={!valid || mutation.isPending} className={valid ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Record correction"}
      </button>
      {mutation.isSuccess && <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>Correction recorded.</p>}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Placeholder tab for forms whose endpoints aren't wired yet
// ---------------------------------------------------------------------------
function PlaceholderTab({ title, section }: { title: string; section: string }) {
  return (
    <div>
      <h3>{title}</h3>
      <Callout>
        This form's endpoint lands in a later increment.{" "}
        <em>Use the Streamlit app for {section} entry until then.</em>
      </Callout>
    </div>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const ManualEntryView = () => {
  const [activeTab, setActiveTab] = useState<TabId>("Snapshot");

  const tabContent: Record<TabId, React.ReactNode> = {
    Snapshot: <SnapshotTab />,
    Correction: <CorrectionTab />,
    "Post / Reply": <PostReplyTab />,
    Classify: <PlaceholderTab title="Classify posts" section="classification" />,
    "Daily reps": <PlaceholderTab title="Daily reps" section="daily activity" />,
    "Stir event": <PlaceholderTab title="Stir conversion event" section="Stir event" />,
    Tester: <PlaceholderTab title="Log a Stir tester" section="tester" />,
    "Needs tagging": <PlaceholderTab title="Posts needing tags" section="tagging queue" />,
    "Needs post ID": <PlaceholderTab title="Posts needing X post ID" section="post ID" />,
  };

  return (
    <>
      <Kicker>DATA ENTRY HUB · §15</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Manual entry</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Spec §15.1–§15.4. Every form here writes directly to the SQLite store.
      </p>

      {/* Tab bar */}
      <div className="tab-list" style={{ marginBottom: "1rem" }}>
        {TABS.map((tab) => (
          <button
            key={tab}
            className="tab"
            aria-selected={tab === activeTab}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tabContent[activeTab]}

      <Hairline />
    </>
  );
};
