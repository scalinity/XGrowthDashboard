/// <reference types="vite/client" />

/**
 * Dev-only sidecar overrides (§31, Step 0). Set in `.env.development.local`
 * (gitignored via `*.local`) so the views can be opened in a plain browser and
 * screenshot-diffed against the Streamlit pages without the Tauri shell. In a
 * real Tauri launch these are unset and the shell's `get_sidecar_info` command
 * supplies the random port + per-launch token instead.
 */
interface ImportMetaEnv {
  readonly VITE_SIDECAR_URL?: string;
  readonly VITE_SIDECAR_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
