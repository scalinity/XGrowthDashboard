/**
 * Instrument-cluster meters — ported from theme.py:
 *  - score_bank (§29.3 R/E/S/O stepped cluster)
 *  - iwh_meter (intelligence/wisdom/humility, stepped segments)
 *  - cost_meter (phosphor → amber@80% → cap@100%)
 *  - token_ttl_countdown (MM:SS, the one animated element)
 */
import type { CSSProperties, ReactNode } from "react";
import { palette, stepColors } from "../theme/tokens";

// --- score_bank --------------------------------------------------------------
const segWrap: CSSProperties = {
  flex: 1,
  padding: "0.45rem 0.5rem 0.4rem",
  background: palette.surface,
  borderRadius: "2px",
};

function ScoreRow({ label, numeral, color }: { label: string; numeral: string; color: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "0.66rem",
          letterSpacing: "0.1em",
          color: palette.boneFaint,
        }}
      >
        {label}
      </span>
      <span className="numeric" style={{ fontSize: "1.15rem", lineHeight: 1, color }}>
        {numeral}
      </span>
    </div>
  );
}

function ScoreSegment({ label, value }: { label: string; value: number | null }) {
  if (value === null) {
    return (
      <div style={segWrap}>
        <div style={{ display: "flex", gap: "1px", marginBottom: "0.2rem" }}>
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              style={{
                flex: 1,
                height: "3px",
                background: "transparent",
                borderTop: `1px dashed ${palette.hairline}`,
              }}
            />
          ))}
        </div>
        <ScoreRow label={label} numeral="—" color={palette.boneFaint} />
      </div>
    );
  }
  const v = Math.max(0, Math.min(3, Math.trunc(value)));
  const color = stepColors[v];
  return (
    <div style={segWrap}>
      <div style={{ display: "flex", gap: "2px", marginBottom: "0.3rem" }}>
        {[0, 1, 2, 3].map((i) => {
          const on = v >= 1 && i <= v;
          return (
            <div key={i} style={{ flex: 1, height: "3px", background: on ? color : palette.hairline }} />
          );
        })}
      </div>
      <ScoreRow label={label} numeral={String(v)} color={color} />
    </div>
  );
}

export function ScoreBank({
  relevance,
  engagementSurface,
  saturation,
  replyOpportunity,
  engagementFootnote,
}: {
  relevance: number | null;
  engagementSurface: number | null;
  saturation: number | null;
  replyOpportunity: number | null;
  engagementFootnote?: string;
}) {
  return (
    <div>
      <div style={{ display: "flex", gap: "0.45rem", margin: "0.45rem 0 0.5rem" }}>
        <ScoreSegment label="R" value={relevance} />
        <ScoreSegment label={engagementFootnote ? "E*" : "E"} value={engagementSurface} />
        <ScoreSegment label="S" value={saturation} />
        <ScoreSegment label="O" value={replyOpportunity} />
      </div>
      {engagementFootnote && (
        <div
          className="faint"
          style={{ fontSize: "0.7rem", margin: "-0.2rem 0 0.3rem 0.1rem" }}
        >
          <span style={{ fontFamily: "var(--font-mono)" }}>*</span> {engagementFootnote}
        </div>
      )}
    </div>
  );
}

// --- iwh_meter ---------------------------------------------------------------
function IwhSegment({ label, value }: { label: string; value: number }) {
  const color = stepColors[Math.max(0, Math.min(3, value))];
  return (
    <div
      style={{
        display: "inline-block",
        width: "28%",
        padding: "0.3rem 0",
        textAlign: "center",
        background: palette.surface,
        borderTop: `2px solid ${color}`,
        marginRight: "1.5%",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "0.7rem",
          letterSpacing: "0.08em",
          color: palette.boneFaint,
        }}
      >
        {label}
      </div>
      <div className="numeric" style={{ fontSize: "1.1rem", color }}>
        {value}
      </div>
    </div>
  );
}

export function IwhMeter({
  intelligence,
  wisdom,
  humility,
}: {
  intelligence: number;
  wisdom: number;
  humility: number;
}) {
  return (
    <div style={{ margin: "0.3rem 0 0.6rem" }}>
      <IwhSegment label="I" value={intelligence} />
      <IwhSegment label="W" value={wisdom} />
      <IwhSegment label="H" value={humility} />
    </div>
  );
}

// --- cost_meter --------------------------------------------------------------
export function CostMeter({ mtdUsd, capUsd }: { mtdUsd: number; capUsd: number }) {
  const pct = capUsd > 0 ? mtdUsd / capUsd : 0;
  const clamped = Math.min(1, Math.max(0, pct));
  let fill: string = palette.phosphorDim;
  let textColor: string = palette.bone;
  let suffix = "";
  if (pct >= 1) {
    fill = palette.confidenceDirectionalBg;
    textColor = palette.confidenceDirectionalBg;
    suffix = " — CAP REACHED";
  } else if (pct >= 0.8) {
    fill = palette.confidenceDirectionalBg;
  }
  return (
    <div>
      <div
        style={{
          background: palette.surface,
          border: `1px solid ${palette.hairline}`,
          height: "0.55rem",
          borderRadius: "1px",
          margin: "0.2rem 0 0.35rem",
        }}
      >
        <div style={{ background: fill, width: `${(clamped * 100).toFixed(1)}%`, height: "100%" }} />
      </div>
      <div
        className="numeric"
        style={{ fontSize: "0.85rem", color: textColor }}
      >
        ${mtdUsd.toFixed(2)} / ${capUsd.toFixed(2)}{" "}
        <span className="faint" style={{ fontSize: "0.75rem" }}>
          ({(pct * 100).toFixed(0)}%){suffix}
        </span>
      </div>
    </div>
  );
}

// --- token_ttl_countdown -----------------------------------------------------
export function TokenTtlCountdown({ secondsRemaining }: { secondsRemaining: number }): ReactNode {
  const s = Math.max(0, Math.trunc(secondsRemaining));
  let color: string = palette.phosphor;
  if (s <= 5) color = palette.boneFaint;
  else if (s <= 10) color = palette.confidenceDirectionalBg;
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return (
    <div>
      <div className="kicker">TOKEN EXPIRES IN</div>
      <div
        className="numeric"
        style={{ fontSize: "2.6rem", letterSpacing: "-0.02em", color, lineHeight: 1 }}
      >
        {mm}:{ss}
      </div>
      <div className="faint" style={{ fontSize: "0.75rem", marginTop: "0.4rem" }}>
        Tokens are single-use, sha256-hashed server-side. Expiry voids the click.
      </div>
    </div>
  );
}
