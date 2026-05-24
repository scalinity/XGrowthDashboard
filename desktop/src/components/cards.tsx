/**
 * Surface cards — ported from theme.py:
 *  - readout_card (instrument readout w/ left keyline)
 *  - specimen_block (immutable raw text, dashed keyline)
 *  - candidate_card (Brain Dump candidate draft)
 *  - console_log_row (Agent Chat sessions list row)
 */
import type { ReactNode } from "react";
import { palette } from "../theme/tokens";
import { StatusChip } from "./badges";

type PaletteKey = keyof typeof palette;

export function ReadoutCard({
  label,
  value,
  caption,
  accent = "phosphor",
  empty = false,
}: {
  label: string;
  value: string;
  caption?: string;
  accent?: PaletteKey;
  empty?: boolean;
}) {
  const borderColor = empty ? palette.hairline : palette[accent];
  const valueColor = empty ? palette.boneDim : palette.bone;
  return (
    <div
      style={{
        padding: "0.6rem 0.9rem",
        margin: "0.4rem 0 0.8rem",
        background: palette.surface,
        borderLeft: `2px ${empty ? "dashed" : "solid"} ${borderColor}`,
        borderRadius: "2px",
      }}
    >
      <div
        className="faint"
        style={{
          fontSize: "0.72rem",
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      <div className="numeric" style={{ fontSize: "1.25rem", color: valueColor, marginTop: "0.15rem" }}>
        {value}
      </div>
      {caption && (
        <div className="faint" style={{ fontSize: "0.78rem", marginTop: "0.1rem" }}>
          {caption}
        </div>
      )}
    </div>
  );
}

export function SpecimenBlock({ text, maxHeightRem = 16 }: { text: string; maxHeightRem?: number }) {
  return (
    <div
      style={{
        padding: "0.7rem 0.9rem",
        margin: "0.4rem 0 0.7rem",
        background: palette.surface,
        borderLeft: `2px dashed ${palette.hairline}`,
        borderRadius: "2px",
        maxHeight: `${maxHeightRem}rem`,
        overflowY: "auto",
      }}
    >
      <pre
        style={{
          margin: 0,
          fontFamily: "var(--font-body)",
          fontSize: "0.92rem",
          lineHeight: 1.45,
          color: palette.bone,
          whiteSpace: "pre-wrap",
          wordWrap: "break-word",
        }}
      >
        {text}
      </pre>
    </div>
  );
}

export function CandidateCard({
  index,
  text,
  pillar,
  audience,
  cta,
  contentType,
  rationale,
  children,
  statusLabel,
}: {
  index: number;
  text: string;
  pillar: string;
  audience: string;
  cta: string;
  contentType: string;
  rationale: string;
  children?: ReactNode;
  statusLabel?: string;
}) {
  return (
    <div style={{ margin: "0.4rem 0" }}>
      <div
        style={{
          padding: "0.7rem 0.9rem",
          background: palette.surface,
          borderLeft: `2px solid ${palette.phosphor}`,
          borderRadius: "2px",
        }}
      >
        <div style={{ marginBottom: "0.45rem" }}>
          <span className="kicker" style={{ color: palette.phosphor }}>
            CANDIDATE {index}
          </span>{" "}
          <span
            className="dim"
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.72rem",
              letterSpacing: "0.06em",
              color: palette.boneFaint,
            }}
          >
            {pillar} · {audience} · {cta} · {contentType}
          </span>
        </div>
        <div style={{ fontSize: "0.96rem", color: palette.bone, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>
          {text}
        </div>
        <div
          style={{
            marginTop: "0.55rem",
            fontStyle: "italic",
            color: palette.boneDim,
            fontSize: "0.82rem",
            fontFamily: "var(--font-display)",
          }}
        >
          {rationale}
        </div>
      </div>
      {statusLabel ? (
        <div style={{ margin: "-0.1rem 0 0.7rem" }}>
          <StatusChip label={statusLabel} tone="done" />
        </div>
      ) : (
        children
      )}
    </div>
  );
}

export function ConsoleLogRow({
  timestamp,
  kind,
  title,
  active = false,
}: {
  timestamp: string;
  kind: string;
  title: string;
  active?: boolean;
}) {
  return (
    <div
      style={{
        borderLeft: `2px solid ${active ? palette.phosphor : palette.hairline}`,
        padding: "0.2rem 0.6rem",
        margin: "0.15rem 0",
      }}
    >
      <span className="numeric" style={{ fontSize: "0.75rem", color: palette.boneFaint }}>
        {timestamp}
      </span>{" "}
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "0.7rem",
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: palette.phosphor,
        }}
      >
        · {kind}
      </span>
      <div
        style={{
          fontSize: "0.85rem",
          color: active ? palette.bone : palette.boneDim,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {title}
      </div>
    </div>
  );
}
