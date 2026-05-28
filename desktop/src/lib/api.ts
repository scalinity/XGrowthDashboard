/**
 * Sidecar API client (spec §31.2/§31.3).
 *
 * The Tauri shell spawns the Python FastAPI sidecar on a random 127.0.0.1 port
 * with a per-launch bearer token, and exposes them via the `get_sidecar_info`
 * command + `sidecar://ready` event. This module turns that into a typed fetch
 * wrapper the views use; the token lives only in memory here, never persisted.
 */
import { invoke } from "@tauri-apps/api/core";

import type {
  AgentModePayload,
  CapabilitiesPayload,
  DiagnosticsCopyResponse,
  HealthDetails,
} from "./contracts";

export type { AgentModePayload, CapabilitiesPayload, HealthDetails } from "./contracts";
export type SidecarPhase = HealthDetails["sidecar_phase"];

export interface SidecarInfo {
  port: number | null;
  token: string | null;
  /**
   * Full base URL when running outside the Tauri shell (dev fallback). In a
   * real Tauri launch this is null and the URL is derived from `port`.
   */
  baseUrl?: string | null;
  ready: boolean;
}

let cached: SidecarInfo | null = null;

/** True only inside the Tauri WKWebView (the shell injects __TAURI_INTERNALS__). */
function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function readInfo(): Promise<SidecarInfo> {
  return invoke<SidecarInfo>("get_sidecar_info");
}

/**
 * Resolve the dev sidecar from Vite env (§31 Step 0). Used only when NOT
 * running under Tauri — e.g. `pnpm dev` in a browser for screenshot-diffing
 * against the Streamlit pages. Requires VITE_SIDECAR_URL + VITE_SIDECAR_TOKEN
 * (set in `.env.development.local`); throws a clear error if absent so the
 * failure mode is obvious rather than a silent 401.
 */
function devSidecarInfo(): SidecarInfo {
  const baseUrl = import.meta.env.VITE_SIDECAR_URL;
  const token = import.meta.env.VITE_SIDECAR_TOKEN;
  if (!baseUrl || !token) {
    throw new Error(
      "Not running under Tauri and VITE_SIDECAR_URL / VITE_SIDECAR_TOKEN are unset. " +
        "Copy desktop/.env.development.example to .env.development.local and start the " +
        "dev sidecar (uv run python -m scripts.dev_sidecar).",
    );
  }
  return { port: null, token, baseUrl: baseUrl.replace(/\/+$/, ""), ready: true };
}

/** Poll the shell until the sidecar handshake completes (default ~60s budget).
 *  The frozen PyInstaller binary can take 30-50s on first launch (extraction +
 *  migrations), so 10s was too aggressive — caused "sidecar did not become
 *  ready in time" on cold starts. */
export async function waitForSidecar(timeoutMs = 60_000): Promise<SidecarInfo> {
  if (cached?.ready) return cached;
  if (!isTauri()) {
    cached = devSidecarInfo();
    return cached;
  }
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const info = await readInfo();
    if (info.ready && info.port != null && info.token != null) {
      cached = info;
      return info;
    }
    if (Date.now() > deadline) {
      throw new Error(
        "The local service did not start in time. Restart the app or copy diagnostics from Settings.",
      );
    }
    await new Promise((r) => setTimeout(r, 150));
  }
}

export function apiBaseUrl(info: SidecarInfo): string {
  if (info.baseUrl) return info.baseUrl;
  return `http://127.0.0.1:${info.port}`;
}

export function userSafeApiError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  if (/bearer|token|sk-|xai-|authorization/i.test(raw)) {
    return "The local service returned an authentication error. Restart the app and try again.";
  }
  if (/500|502|503|504/.test(raw)) {
    return "The local service hit an internal error. Copy diagnostics from Settings if it persists.";
  }
  if (/401|403/.test(raw)) {
    return "The local service rejected the request. Restart the app and try again.";
  }
  if (/did not start in time/i.test(raw)) {
    return raw;
  }
  if (/Couldn't reach|Load failed|Failed to fetch|NetworkError/i.test(raw)) {
    return "Couldn't reach the local service. Confirm the app finished launching, then retry.";
  }
  return raw.length > 180 ? `${raw.slice(0, 180)}…` : raw;
}

/** Authenticated JSON fetch against the sidecar. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const info = await waitForSidecar();
  const res = await fetch(`${apiBaseUrl(info)}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${info.token}`,
      // S1: only set Content-Type when there's a body (POST/PUT).
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    const technical = `${res.status} ${res.statusText}: ${body}`;
    console.error(`apiFetch ${path} failed:`, technical);
    throw new Error(userSafeApiError(new Error(technical)));
  }
  return (await res.json()) as T;
}

export async function fetchHealthDetails(): Promise<HealthDetails> {
  return apiFetch<HealthDetails>("/health/details");
}

export async function fetchAgentMode(): Promise<AgentModePayload> {
  return apiFetch<AgentModePayload>("/agent/mode");
}

export async function fetchCapabilities(): Promise<CapabilitiesPayload> {
  return apiFetch<CapabilitiesPayload>("/capabilities");
}

export async function copyDiagnosticsToClipboard(): Promise<string> {
  const payload = await apiFetch<DiagnosticsCopyResponse>("/diagnostics/copy");
  const text = payload.text;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
  }
  return text;
}

export async function fetchToday() {
  return apiFetch<import("./contracts").TodayResponse>("/views/today");
}

export async function fetchReplyQueue() {
  return apiFetch<import("./contracts").ReplyQueueResponse>("/views/reply-queue");
}

export async function fetchSettings() {
  return apiFetch<import("./contracts").SettingsResponse>("/settings");
}

// S3: removed dead api.* convenience wrappers — views use apiFetch<T> directly.
