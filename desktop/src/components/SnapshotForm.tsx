/**
 * Shared snapshot form — used by both TodayView (pinned form) and
 * ManualEntryView (Snapshot tab). Fetches defaults from /views/today
 * so username/profile_url/baseline are always correct (RV5-W1 fix).
 *
 * No useEffect — useMutation for submit.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "../lib/api";
import { palette } from "../theme/tokens";

interface SnapshotDefaults {
  username: string;
  profile_url: string;
  baseline_followers: number;
  x_user_id: string | null;
}

export function SnapshotForm({
  defaults,
  onSuccess,
}: {
  defaults?: SnapshotDefaults;
  onSuccess?: () => void;
}) {
  const queryClient = useQueryClient();
  const [followers, setFollowers] = useState("");
  const [following, setFollowing] = useState("");
  const [posts, setPosts] = useState("");
  const [listed, setListed] = useState("");

  // If no defaults provided, fetch from the today endpoint.
  const { data: todayData } = useQuery({
    queryKey: ["today-defaults"],
    queryFn: () => apiFetch<{ snapshot_defaults: SnapshotDefaults }>("/views/today"),
    enabled: !defaults,
    select: (d) => d.snapshot_defaults,
  });

  const resolvedDefaults = defaults ?? todayData ?? {
    username: "",
    profile_url: "",
    baseline_followers: 0,
    x_user_id: null,
  };

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ snapshot_id: number }>("/forms/snapshot", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["today"] });
      queryClient.invalidateQueries({ queryKey: ["progress"] });
      queryClient.invalidateQueries({ queryKey: ["today-defaults"] });
      setFollowers(""); setFollowing(""); setPosts(""); setListed("");
      onSuccess?.();
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      snapshot_date: new Date().toISOString().slice(0, 10),
      username: resolvedDefaults.username,
      profile_url: resolvedDefaults.profile_url,
      baseline_followers: resolvedDefaults.baseline_followers,
      x_user_id: resolvedDefaults.x_user_id,
      followers_count: parseInt(followers, 10),
      following_count: parseInt(following, 10),
      post_count: parseInt(posts, 10),
      listed_count: parseInt(listed, 10) || 0,
    });
  };

  const valid =
    followers !== "" && following !== "" && posts !== "" &&
    !isNaN(parseInt(followers)) && !isNaN(parseInt(following)) && !isNaN(parseInt(posts));

  return (
    <div style={{ margin: "0.6rem 0 1rem" }}>
      <h3>Pinned daily snapshot</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Spec §15.1 — designed to take 30 seconds. Sets source='manual', data_quality='manual'.
        Corrections never overwrite (§13 hard rule 2).
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
