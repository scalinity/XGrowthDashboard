/**
 * Chip/badge components — ported from theme.py:
 *  - recommended_action_badge (stepped ladder, NO RED)
 *  - status_chip (neutral/active/warn/done/failed)
 *  - citation_chip (surviving vs stripped)
 *  - confidence badge (4-tier colorblind-friendly)
 */
import type { CSSProperties } from "react";
import { palette } from "../theme/tokens";

/** Base mono chip styling shared by every badge (matches theme.py spans). */
function chipStyle(bg: string, fg: string, extra?: CSSProperties): CSSProperties {
  return {
    fontFamily: "var(--font-mono)",
    fontSize: "0.74rem",
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    background: bg,
    color: fg,
    padding: "2px 8px",
    borderRadius: "2px",
    ...extra,
  };
}

// --- Confidence (4-tier) -----------------------------------------------------
const CONFIDENCE_TIERS = {
  insufficient: [palette.confidenceInsufficientBg, palette.confidenceInsufficientFg],
  directional: [palette.confidenceDirectionalBg, palette.confidenceDirectionalFg],
  tentative: [palette.confidenceTentativeBg, palette.confidenceTentativeFg],
  confident: [palette.confidenceConfidentBg, palette.confidenceConfidentFg],
} as const;

export type ConfidenceTier = keyof typeof CONFIDENCE_TIERS;

export function ConfidenceBadge({ tier, label }: { tier: ConfidenceTier; label?: string }) {
  const [bg, fg] = CONFIDENCE_TIERS[tier];
  return <span style={chipStyle(bg, fg)}>{label ?? tier}</span>;
}

// --- Recommended action (stepped ladder, no red) -----------------------------
const ACTION_STYLES: Record<string, [string, string, CSSProperties]> = {
  reply_now: [palette.phosphor, palette.ink, {}],
  reply_if_time: [palette.phosphorDim, palette.bone, {}],
  consider: [palette.surfaceRaised, palette.boneDim, {}],
  skip: [palette.surface, palette.boneFaint, { textDecoration: "line-through" }],
};

export function RecommendedActionBadge({ label }: { label: string | null }) {
  if (!label) {
    return <span style={chipStyle(palette.surface, palette.boneFaint)}>unscored</span>;
  }
  const [bg, fg, extra] = ACTION_STYLES[label] ?? [palette.surface, palette.boneDim, {}];
  return <span style={chipStyle(bg, fg, extra)}>{label.replace(/_/g, " ")}</span>;
}

// --- Status chip -------------------------------------------------------------
const TONES: Record<string, [string, string]> = {
  neutral: [palette.surfaceRaised, palette.boneDim],
  active: [palette.phosphorDim, palette.bone],
  warn: [palette.warnAmber, palette.ink],
  done: [palette.surface, palette.phosphor],
  failed: [palette.warnAmber, palette.ink],
};

export type StatusTone = keyof typeof TONES;

export function StatusChip({ label, tone = "neutral" }: { label: string; tone?: StatusTone }) {
  const [bg, fg] = TONES[tone] ?? TONES.neutral;
  return <span style={chipStyle(bg, fg)}>{label}</span>;
}

// --- Citation chip (§28.23) --------------------------------------------------
export function CitationChip({
  recordType,
  idOrFilter,
  stripped = false,
}: {
  recordType: string;
  idOrFilter: string;
  stripped?: boolean;
}) {
  const fg = stripped ? palette.warnAmber : palette.phosphor;
  return (
    <span
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "0.74rem",
        letterSpacing: "0.06em",
        background: palette.surface,
        color: fg,
        padding: "1px 7px",
        border: `1px solid ${palette.hairline}`,
        borderRadius: "2px",
        margin: "0 0.15rem",
        textDecoration: stripped ? "line-through" : "none",
      }}
    >
      〔{recordType} {idOrFilter}〕
    </span>
  );
}

// --- Prepublish composite-label chip (§28.11) --------------------------------
const PREPUBLISH_STYLES: Record<string, [string, string]> = {
  weak: [palette.confidenceDirectionalBg, palette.confidenceDirectionalFg],
  viable: [palette.confidenceTentativeBg, palette.confidenceTentativeFg],
  strong: [palette.confidenceConfidentBg, palette.confidenceConfidentFg],
};

export function PrepublishChip({ label }: { label: string | null }) {
  if (!label) return null;
  const key = label.trim().toLowerCase();
  const [bg, fg] = PREPUBLISH_STYLES[key] ?? [palette.surface, palette.boneDim];
  return <span style={chipStyle(bg, fg, { fontWeight: 600 })}>PRE-PUBLISH · {key}</span>;
}

// --- Repetition guard banner (§28.13) ----------------------------------------
interface SimilarityWarning {
  label?: string;
  max_cosine?: number;
  nearest_post_id?: number;
  nearest_text_excerpt?: string;
}

const REP_LABELS: Record<string, [string, string]> = {
  near_duplicate: ["NEAR DUPLICATE", "You've shipped almost exactly this idea before. Decide consciously."],
  close_echo: ["CLOSE ECHO", "Similar to a recent post. Worth a glance before publishing."],
};

export function RepetitionBanner({ warningJson }: { warningJson: string | Record<string, unknown> | null }) {
  if (!warningJson) return null;
  let warning: SimilarityWarning;
  if (typeof warningJson === "string") {
    try {
      warning = JSON.parse(warningJson);
    } catch {
      return null;
    }
  } else {
    warning = warningJson as SimilarityWarning;
  }
  const label = (warning.label ?? "").toLowerCase();
  const match = REP_LABELS[label];
  if (!match) return null;
  const [chipLabel, summary] = match;
  const cosine = typeof warning.max_cosine === "number" ? warning.max_cosine.toFixed(2) : "—";
  const excerpt = warning.nearest_text_excerpt ?? "";
  const nearestId = warning.nearest_post_id;

  return (
    <div
      style={{
        borderLeft: `3px solid ${palette.warnAmber}`,
        background: palette.surfaceRaised,
        padding: "0.5rem 0.8rem",
        margin: "0.4rem 0",
      }}
    >
      <div
        className="numeric"
        style={{
          fontSize: "0.7rem",
          letterSpacing: "0.08em",
          color: palette.warnAmber,
          textTransform: "uppercase",
        }}
      >
        REPETITION GUARD · {chipLabel} · COSINE {cosine}
      </div>
      <div style={{ fontSize: "0.85rem", color: palette.bone, marginTop: "0.3rem" }}>
        {summary}
      </div>
      <div
        style={{
          fontFamily: "var(--font-display)",
          fontSize: "0.95rem",
          color: palette.boneDim,
          marginTop: "0.4rem",
          fontStyle: "italic",
        }}
      >
        Nearest post #{nearestId ?? "?"}: {excerpt}
      </div>
    </div>
  );
}
