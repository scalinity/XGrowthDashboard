/**
 * Primitive helpers — ported from theme.py kicker/hairline/callout/dim/numeric.
 * Styling lives in tokens.css (.kicker/.hairline/.callout/.numeric/.dim).
 */
import type { CSSProperties, ReactNode } from "react";

export function Kicker({ children, color }: { children: ReactNode; color?: string }) {
  const style: CSSProperties | undefined = color ? { color } : undefined;
  return (
    <div className="kicker" style={style}>
      {children}
    </div>
  );
}

export function Hairline() {
  return <hr className="hairline" />;
}

/** A small phosphor-edged callout. Use <em> for the one encouraged emphasis. */
export function Callout({ children }: { children: ReactNode }) {
  return <div className="callout">{children}</div>;
}

/** Inline tabular-figures span — for numbers inside larger text. */
export function Numeric({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <span className="numeric" style={style}>
      {children}
    </span>
  );
}

/** Inline dim label. */
export function Dim({ children }: { children: ReactNode }) {
  return <span className="dim">{children}</span>;
}
