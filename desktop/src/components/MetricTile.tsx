/**
 * MetricTile — matches theme.py's stMetric styling (§31.4):
 *   - Label: IBM Plex Sans, 0.78rem, uppercase, letter-spacing 0.06em, bone_dim
 *   - Value: JetBrains Mono, weight 500, 2rem, bone, letter-spacing -0.02em
 *   - Delta: JetBrains Mono, weight 500, 0.9rem (optional sub-caption)
 *
 * Unlike ReadoutCard (which has a left keyline for instrument-cluster readouts),
 * MetricTile is borderless — matching Streamlit's st.metric layout exactly.
 */
import { palette } from "../theme/tokens";

export function MetricTile({
  label,
  value,
  delta,
  deltaCaption,
}: {
  label: string;
  value: string;
  delta?: string;
  deltaCaption?: string;
}) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div
        style={{
          fontFamily: "var(--font-body)",
          fontSize: "0.78rem",
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: palette.boneDim,
        }}
      >
        {label}
      </div>
      <div
        className="numeric"
        style={{
          fontSize: "2rem",
          fontWeight: 500,
          letterSpacing: "-0.02em",
          color: palette.bone,
          lineHeight: 1.15,
          marginTop: "0.1rem",
        }}
      >
        {value}
      </div>
      {delta && (
        <div
          className="numeric"
          style={{
            fontSize: "0.9rem",
            fontWeight: 500,
            color: palette.boneDim,
            marginTop: "0.15rem",
          }}
        >
          {delta}
        </div>
      )}
      {deltaCaption && (
        <div
          className="faint"
          style={{
            fontSize: "0.78rem",
            marginTop: "0.05rem",
          }}
        >
          {deltaCaption}
        </div>
      )}
    </div>
  );
}

/** 4-across metric row — mirrors Streamlit's `st.columns(4)` with metrics. */
export function MetricRow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: "1.2rem", marginBottom: "1rem" }}>
      {children}
    </div>
  );
}

/** Progress bar — mirrors Streamlit's st.progress, instrument-panel styled. */
export function ProgressBar({
  value,
  label,
}: {
  value: number;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <div style={{ margin: "0.3rem 0 0.6rem" }}>
      <div
        style={{
          height: "0.5rem",
          background: palette.surface,
          border: `1px solid ${palette.hairline}`,
          borderRadius: "2px",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${(pct * 100).toFixed(1)}%`,
            background: palette.phosphor,
            borderRadius: "1px",
            transition: "width 200ms ease",
          }}
        />
      </div>
      {label && (
        <div
          className="numeric"
          style={{
            fontSize: "0.82rem",
            color: palette.boneDim,
            marginTop: "0.2rem",
          }}
        >
          {label}
        </div>
      )}
    </div>
  );
}
