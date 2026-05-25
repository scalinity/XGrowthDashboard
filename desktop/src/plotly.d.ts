// Ambient type declaration for plotly.js-dist-min (no @types package ships).
// The actual types come from @types/react-plotly.js which re-exports plotly.js types.
declare module "plotly.js-dist-min" {
  import Plotly from "plotly.js";
  export default Plotly;
  export * from "plotly.js";
}
