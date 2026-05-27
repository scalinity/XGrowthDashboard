/**
 * App shell — instrument-panel chrome (spec §31.4). A left sidebar nav mirrors
 * the Streamlit page nav (the 18 views, grouped), surface-toned with a hairline
 * border-right exactly like theme.py's [data-testid="stSidebar"]. The content
 * pane holds the active view.
 */
import type { ReactNode } from "react";
import { CapabilitiesBanner } from "./CapabilitiesBanner";
import { palette } from "../theme/tokens";
import type { ViewDef } from "../views";

const GROUP_ORDER = ["Analytics", "Manual", "Agent", "Growth"] as const;

export function Layout({
  views,
  activeId,
  onSelect,
  children,
}: {
  views: ViewDef[];
  activeId: string;
  onSelect: (id: string) => void;
  children: ReactNode;
}) {
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <aside
        style={{
          width: 248,
          flexShrink: 0,
          background: palette.surface,
          borderRight: `1px solid ${palette.hairline}`,
          padding: "1.4rem 0.9rem",
          overflowY: "auto",
        }}
      >
        <div className="kicker" style={{ color: palette.phosphor, marginBottom: "0.2rem" }}>
          X Growth
        </div>
        <div
          style={{
            fontFamily: "var(--font-display)",
            fontSize: "1.1rem",
            color: palette.bone,
            marginBottom: "1.2rem",
          }}
        >
          Dashboard
        </div>

        {GROUP_ORDER.map((group) => {
          const items = views.filter((v) => v.group === group);
          if (!items.length) return null;
          return (
            <div key={group} style={{ marginBottom: "1.1rem" }}>
              <div className="kicker" style={{ marginBottom: "0.4rem" }}>
                {group}
              </div>
              {items.map((v) => {
                const active = v.id === activeId;
                return (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => onSelect(v.id)}
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      background: active ? palette.surfaceRaised : "transparent",
                      border: 0,
                      borderLeft: `2px solid ${active ? palette.phosphor : "transparent"}`,
                      color: active ? palette.bone : palette.boneDim,
                      fontFamily: "var(--font-body)",
                      fontSize: "0.86rem",
                      padding: "0.32rem 0.6rem",
                      borderRadius: 0,
                      cursor: "pointer",
                    }}
                  >
                    {v.label}
                  </button>
                );
              })}
            </div>
          );
        })}
      </aside>

      <div
        style={{
          flex: 1,
          minWidth: 0,
          width: "100%",
          padding: "2.2rem 2.4rem 4rem",
          overflowX: "hidden",
        }}
      >
        <CapabilitiesBanner />
        {children}
      </div>
    </div>
  );
}
