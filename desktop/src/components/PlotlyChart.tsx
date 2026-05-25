/**
 * Plotly.js wrapper — renders a figure from the Python chart module's
 * fig.to_dict() JSON (spec §31.4: "effectively byte-identical charts").
 *
 * Uses plotly.js-dist-min directly with a ref callback pattern (no
 * useEffect). When `figure` changes, React re-invokes the ref callback
 * with the existing DOM node → Plotly.react updates the chart in-place.
 * On unmount, the callback receives null → we purge the old plot.
 */
import { useCallback, useRef } from "react";
import Plotly from "plotly.js-dist-min";

interface PlotlyFigure {
  data: Plotly.Data[];
  layout?: Partial<Plotly.Layout>;
}

export function PlotlyChart({
  figure,
  style,
}: {
  figure: PlotlyFigure;
  style?: React.CSSProperties;
}) {
  const plotDivRef = useRef<HTMLDivElement | null>(null);

  // Ref callback — no useEffect needed. React calls this when:
  //   (a) the div mounts (node = element) → we render the chart,
  //   (b) the callback identity changes because `figure` changed →
  //       React calls old-callback(null) then new-callback(element), or
  //   (c) the div unmounts (node = null) → we purge the plot.
  // This is the "third-party widget sync" pattern using ref callbacks
  // instead of useEffect (per the project React side-effects discipline).
  const refCallback = useCallback(
    (node: HTMLDivElement | null) => {
      // Purge old node if it changed.
      if (plotDivRef.current && plotDivRef.current !== node) {
        Plotly.purge(plotDivRef.current);
      }
      plotDivRef.current = node;

      if (node && figure) {
        Plotly.react(
          node,
          figure.data,
          { ...(figure.layout ?? {}), autosize: true } as Partial<Plotly.Layout>,
          { displayModeBar: false, responsive: true },
        );
      }
    },
    [figure],
  );

  return <div ref={refCallback} style={{ width: "100%", ...style }} />;
}
