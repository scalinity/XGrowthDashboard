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
  ready: boolean;
}

let cached: SidecarInfo | null = null;

async function readInfo(): Promise<SidecarInfo> {
  return invoke<SidecarInfo>("get_sidecar_info");
}

/** Poll the shell until the sidecar handshake completes (default ~10s budget). */
export async function waitForSidecar(timeoutMs = 10_000): Promise<SidecarInfo> {
  if (cached?.ready) return cached;
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
  return `http://127.0.0.1:${info.port}`;
}

/** Authenticated JSON fetch against the sidecar. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const info = await waitForSidecar();
  const res = await fetch(`${apiBaseUrl(info)}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${info.token}`,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => apiFetch<{ status: string }>("/health"),
  today: () => apiFetch<Record<string, unknown>>("/views/today"),
  nextRep: () => apiFetch<Record<string, unknown>>("/views/next-rep"),
  validation: () => apiFetch<Record<string, unknown>>("/views/validation"),
  settings: () => apiFetch<{ settings: Record<string, unknown> }>("/settings"),
};
