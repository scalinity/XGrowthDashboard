/**
 * Settings — faithful port of app/pages/7_Settings.py (spec §14.7).
 *
 * Grouped settings panels matching the Streamlit page's _GROUPS structure.
 * Uses existing GET /settings + PUT /settings/{key} endpoints.
 * No useEffect — TanStack Query for reads, useMutation for writes.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { apiFetch } from "../lib/api";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Settings groups — mirrors the _GROUPS definition in 7_Settings.py (§14.7).
// This is a presentation concern (which keys go in which section), not
// business logic, so it lives in the frontend.
// ---------------------------------------------------------------------------
interface SettingDef {
  key: string;
  editable: boolean;
  help: string;
}

const GROUPS: Array<{ label: string; keys: SettingDef[] }> = [
  {
    label: "Account",
    keys: [
      { key: "x_handle", editable: true, help: "Public X handle without @ (§2)." },
      { key: "x_user_id", editable: true, help: "Stable X user identifier." },
      { key: "profile_url", editable: true, help: "Public profile URL." },
      { key: "baseline_followers", editable: true, help: "Followers at project start (§2)." },
      { key: "timezone", editable: true, help: "Daily snapshot ritual timezone (§14.7)." },
      { key: "daily_snapshot_time", editable: true, help: "Default snapshot capture time." },
    ],
  },
  {
    label: "Goals",
    keys: [
      { key: "operational_ceiling", editable: true, help: "Operational anchor (default 5,000) (§27)." },
      { key: "long_arc_reminder", editable: true, help: "Display-only long-arc reminder (§27)." },
      { key: "current_milestone", editable: true, help: "Active distribution-ladder target." },
    ],
  },
  {
    label: "Daily reps",
    keys: [
      { key: "daily_post_target", editable: true, help: "Posts/day target (§14.1)." },
      { key: "daily_reply_target", editable: true, help: "Replies/day target (default 12)." },
      { key: "daily_reply_session_target", editable: true, help: "Reply sessions/day target." },
      { key: "target_calibration_review_date", editable: true, help: "Review reply-target adherence on this date." },
    ],
  },
  {
    label: "Accuracy thresholds",
    keys: [
      { key: "lane_sample_size_insufficient", editable: true, help: "post_count<X → insufficient (§11)." },
      { key: "lane_sample_size_low", editable: true, help: "post_count<X → low / scatter-only (§11)." },
      { key: "lane_sample_size_stronger", editable: true, help: "post_count≥X AND days≥14 → confident (§11)." },
      { key: "lane_days_covered_minimum", editable: true, help: "days_covered<X → insufficient (§11)." },
      { key: "velocity_7d_display_threshold", editable: true, help: "|Δ7d|≥X required to show velocity (§13)." },
      { key: "counterfactual_required", editable: true, help: "Weekly review blocks export until counterfactual filled." },
    ],
  },
  {
    label: "Data sources",
    keys: [
      { key: "data_collection_mode", editable: true, help: "manual | api — toggle for scheduled jobs." },
    ],
  },
  {
    label: "Exports & backups",
    keys: [
      { key: "backup_dir", editable: true, help: "VACUUM INTO target directory (§18 rule 10)." },
      { key: "export_dir", editable: true, help: "CSV/Markdown export output folder." },
      { key: "weekly_report_export_path", editable: true, help: "Folder for Markdown weekly reports." },
    ],
  },
];

// ---------------------------------------------------------------------------
// Single setting row component
// ---------------------------------------------------------------------------
function SettingRow({
  settingKey,
  value,
  editable,
  help,
}: {
  settingKey: string;
  value: unknown;
  editable: boolean;
  help: string;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value ?? ""));

  const mutation = useMutation({
    mutationFn: (newValue: unknown) =>
      apiFetch(`/settings/${settingKey}`, {
        method: "PUT",
        body: JSON.stringify({ value: newValue }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      setEditing(false);
    },
  });

  const handleSave = () => {
    // Parse the value: try number, then bool, then string.
    let parsed: unknown = draft;
    if (draft === "true") parsed = true;
    else if (draft === "false") parsed = false;
    else if (draft !== "" && !isNaN(Number(draft))) parsed = Number(draft);
    else if (draft.trim() === "") parsed = null;
    mutation.mutate(parsed);
  };

  const displayValue = value === null || value === undefined ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);

  return (
    <div style={{ padding: "0.5rem 0", borderBottom: `1px solid ${palette.hairline}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="numeric" style={{ fontSize: "0.92rem", color: palette.bone }}>
          {settingKey}
        </span>
        {!editable && (
          <span className="faint" style={{ fontSize: "0.78rem" }}>read-only</span>
        )}
      </div>
      <p className="faint" style={{ margin: "0.1rem 0 0.4rem", fontSize: "0.82rem" }}>
        {help}
      </p>
      {!editable ? (
        <span className="numeric" style={{ color: palette.boneDim }}>{displayValue}</span>
      ) : editing ? (
        <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="primary" onClick={handleSave} disabled={mutation.isPending}>
            {mutation.isPending ? "…" : "Save"}
          </button>
          <button onClick={() => { setEditing(false); setDraft(String(value ?? "")); }}>
            Cancel
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          <span className="numeric" style={{ color: palette.bone, flex: 1 }}>{displayValue}</span>
          <button onClick={() => { setDraft(String(value ?? "")); setEditing(true); }}>
            Edit
          </button>
        </div>
      )}
      {mutation.isError && (
        <div style={{ color: palette.warnAmber, fontSize: "0.85rem", marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const SettingsView = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiFetch<{ settings: Record<string, unknown> }>("/settings"),
    retry: 1,
  });

  if (isLoading) return <p className="dim">Reading the local service…</p>;
  if (error) {
    return (
      <Callout>
        Couldn't reach the local service. <em>{String((error as Error).message ?? error)}</em>
      </Callout>
    );
  }
  if (!data) return null;

  const settings = data.settings;

  return (
    <>
      <Kicker>CONFIGURATION · §14.7</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Settings</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Every settings key from §10.2, grouped per §14.7. Configurable keys persist
        on save via the sidecar. Read-only keys are shown but not editable.
      </p>

      {GROUPS.map((group) => (
        <div key={group.label}>
          <h2>{group.label}</h2>
          {group.keys.map((def) => (
            <SettingRow
              key={def.key}
              settingKey={def.key}
              value={settings[def.key]}
              editable={def.editable}
              help={def.help}
            />
          ))}
          <Hairline />
        </div>
      ))}
    </>
  );
};
