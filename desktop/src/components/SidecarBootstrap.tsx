/**
 * Sidecar bootstrap gate — explicit startup phases for the native shell.
 */
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo } from "react";

import { Callout } from ".";
import { fetchHealthDetails, waitForSidecar, type SidecarPhase } from "../lib/api";
import { palette } from "../theme/tokens";

function phaseLabel(phase: SidecarPhase): string {
  switch (phase) {
    case "launching_sidecar":
      return "Launching local service…";
    case "applying_migrations":
      return "Applying database migrations…";
    case "connecting_db":
      return "Connecting to database…";
    case "ready":
      return "Ready";
    case "failed":
      return "Startup failed";
    default:
      return "Starting…";
  }
}

export function SidecarBootstrap({ children }: { children: ReactNode }) {
  const bootstrap = useQuery({
    queryKey: ["sidecar-bootstrap"],
    queryFn: async () => {
      await waitForSidecar();
      const details = await fetchHealthDetails();
      return details;
    },
    retry: false,
    refetchOnWindowFocus: false,
  });

  const phase: SidecarPhase = useMemo(() => {
    if (bootstrap.isError) return "failed";
    if (bootstrap.isLoading || bootstrap.isFetching) return "launching_sidecar";
    if (bootstrap.data?.ready) return "ready";
    return bootstrap.data?.sidecar_phase ?? "connecting_db";
  }, [bootstrap.data, bootstrap.isError, bootstrap.isFetching, bootstrap.isLoading]);

  if (phase === "ready") return <>{children}</>;

  if (phase === "failed") {
    return (
      <div style={{ padding: "2rem", maxWidth: 720 }}>
        <Callout>
          <strong>{phaseLabel("failed")}</strong>
          <p style={{ marginTop: "0.6rem" }}>
            The local service did not start in time. Open Settings → Copy diagnostics after retrying,
            or restart the app.
          </p>
          <p style={{ color: palette.warnAmber, marginTop: "0.6rem" }}>
            {bootstrap.error instanceof Error ? bootstrap.error.message : String(bootstrap.error)}
          </p>
        </Callout>
      </div>
    );
  }

  return (
    <div style={{ padding: "2rem" }}>
      <p className="dim">{phaseLabel(phase)}</p>
      <p className="dim" style={{ fontSize: "0.85rem", marginTop: "0.4rem" }}>
        First launch can take up to a minute while the bundled service starts.
      </p>
    </div>
  );
}
