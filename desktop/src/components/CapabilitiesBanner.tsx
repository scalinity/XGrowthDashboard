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
  const { data } = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => apiFetch<CapabilitiesPayload>("/capabilities"),
    retry: 1,
  });

  if (!data) return null;

  const missing = unavailableLabels(data);
  if (missing.length === 0) return null;

  return (
    <Callout>
      <strong>Some features are limited</strong>
      <p style={{ marginTop: "0.45rem", fontSize: "0.88rem" }}>
        Unavailable: {missing.join(" · ")}. Configure API keys in Settings or switch data
        collection mode if reads are disabled.
      </p>
    </Callout>
  );
}
