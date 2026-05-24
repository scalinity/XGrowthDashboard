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
export function chipStyle(bg: string, fg: string, extra?: CSSProperties): CSSProperties {
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
