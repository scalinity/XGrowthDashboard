/**
 * Hand-maintained API contracts mirroring app/service/models.py (§31, Phase G).
 * Keep in sync with backend response models and contract snapshot tests.
 */

export interface ApiError {
  detail: string;
}

export interface CapabilityEntry {
  available: boolean;
  label: string;
}

export type CapabilitiesPayload = Record<string, CapabilityEntry>;

export interface AgentModeNicheGate {
  blocked: boolean;
  niche_problem_set: boolean;
  niche_person_set: boolean;
}

export interface AgentModeLintGate {
  reply_quality_lint_enabled: boolean;
  reply_intent_required: boolean;
}

export interface AgentModeSecretState {
  configured: boolean;
}

export interface AgentModeToolPermissions {
  read_dashboard: boolean;
  read_x_api: boolean;
  write_drafts: boolean;
  publish: boolean;
  secrets: boolean;
}

export interface AgentModePayload {
  data_collection_mode: string;
  api_read: boolean;
  publish_mode: string;
  niche_gate: AgentModeNicheGate;
  lint_gate: AgentModeLintGate;
  secret_state: Record<string, AgentModeSecretState>;
  tool_permissions: AgentModeToolPermissions;
}

export interface HealthDetails {
  ready: boolean;
  sidecar_phase:
    | "launching_sidecar"
    | "applying_migrations"
    | "connecting_db"
    | "ready"
    | "failed";
  app_version: string;
  service_version: string;
  db_path: string;
  latest_migration: string | null;
  data_dir_source: string;
  resource_root: string;
  capabilities: Record<string, unknown>;
}

export interface SettingsResponse {
  settings: Record<string, unknown>;
}

export interface SecretsResponse {
  secrets: Record<string, { present: boolean }>;
}

export interface DiagnosticsCopyResponse {
  text: string;
}
