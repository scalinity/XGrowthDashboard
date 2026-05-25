/**
 * Sidecar API client (spec §31.2/§31.3).
 *
 * The Tauri shell spawns the Python FastAPI sidecar on a random 127.0.0.1 port
 * with a per-launch bearer token, and exposes them via the `get_sidecar_info`
 * command + `sidecar://ready` event. This module turns that into a typed fetch
 * wrapper the views use; the token lives only in memory here, never persisted.
 */
import { invoke } from "@tauri-apps/api/core";

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

/** Poll the shell until the sidecar handshake completes (default ~10s budget). */
export async function waitForSidecar(timeoutMs = 10_000): Promise<SidecarInfo> {
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
    if (Date.now() > deadline) throw new Error("sidecar did not become ready in time");
    await new Promise((r) => setTimeout(r, 150));
  }
}

export function apiBaseUrl(info: SidecarInfo): string {
  if (info.baseUrl) return info.baseUrl;
  return `http://127.0.0.1:${info.port}`;
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
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return (await res.json()) as T;
}

// S3: removed dead api.* convenience wrappers — views use apiFetch<T> directly.
