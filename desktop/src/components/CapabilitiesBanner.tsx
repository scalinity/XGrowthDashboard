/**
 * Offline / degraded dependency banner (§28, Phase F P3.5).
 * No useEffect — TanStack Query fetch on mount.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout } from ".";
import { apiFetch } from "../lib/api";
import type { CapabilitiesPayload } from "../lib/contracts";

function unavailableLabels(caps: CapabilitiesPayload): string[] {
  return Object.entries(caps)
    .filter(([, entry]) => !entry.available)
    .map(([, entry]) => entry.label);
}

export function CapabilitiesBanner() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => apiFetch<CapabilitiesPayload>("/capabilities"),
    retry: 1,
  });

  if (isLoading) {
    return <p className="dim capabilities-banner">Checking local service capabilities…</p>;
  }

  if (isError) {
    return (
      <Callout>
        <strong>Could not load capability status</strong>
        <p style={{ marginTop: "0.45rem", fontSize: "0.88rem" }}>
          {error instanceof Error ? error.message : String(error)}
        </p>
        <button type="button" className="secondary" style={{ marginTop: "0.5rem" }} onClick={() => refetch()}>
          Retry
        </button>
      </Callout>
    );
  }

  if (!data) return null;

  const missing = unavailableLabels(data);
  if (missing.length === 0) return null;

  return (
    <Callout>
      <strong>Some features are limited</strong>
      <p className="capabilities-banner__body">
        Unavailable: {missing.join(" · ")}. Configure API keys in Settings or switch data
        collection mode if reads are disabled.
      </p>
    </Callout>
  );
}
