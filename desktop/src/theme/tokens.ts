/**
 * Design tokens — ported 1:1 from app/components/theme.py PALETTE (spec §31.4).
 *
 * This is the SINGLE source of color/typography truth for the native frontend,
 * mirroring the "no inlined hex, extend the palette" rule from theme.py. Every
 * component references `palette` / `fonts`; nothing inlines a raw hex literal.
 *
 * NO RED — sample-size labels frame a question about evidence, not failure.
 */

export const palette = {
  // Surfaces.
  ink: "#0e1116",
  surface: "#161a20",
  surfaceRaised: "#1c2128",
  hairline: "#2a2f37",

  // Type.
  bone: "#e6e1d8",
  boneDim: "#a8a39a",
  boneFaint: "#6c6960",

  // Accents.
  phosphor: "#5fb3a1",
  phosphorDim: "#3d7a6c",

  // Confidence badges — colorblind-friendly four-tier (gray-amber-blue-green).
  confidenceInsufficientBg: "#2a2f37",
  confidenceInsufficientFg: "#a8a39a",
  confidenceDirectionalBg: "#c98b16",
  confidenceDirectionalFg: "#0e1116",
  confidenceTentativeBg: "#3a73e0",
  confidenceTentativeFg: "#ffffff",
  confidenceConfidentBg: "#2da564",
  confidenceConfidentFg: "#0e1116",

  // Generic soft-warning amber (semantic alias of confidence_directional_bg).
  warnAmber: "#c98b16",

  // Functional.
  noiseBand: "rgba(95, 179, 161, 0.12)",
} as const;

/** Lane-scatter palette — colorblind-friendly cool half of the wheel, never red. */
export const laneScatterColors: readonly string[] = [
  "#5fb3a1", // phosphor (primary)
  "#3a73e0", // tentative-blue
  "#c98b16", // directional-amber
  "#2da564", // confident-green
  "#a8a39a", // bone_dim
  "#a87fce", // muted plum
  "#3b8a8a", // deep teal
  "#7ecfd9", // pale cyan
];

export const fonts = {
  display: "'Fraunces Variable', 'Fraunces', 'IBM Plex Serif', Georgia, serif",
  body: "'IBM Plex Sans', -apple-system, sans-serif",
  mono: "'JetBrains Mono', 'IBM Plex Mono', monospace",
} as const;

/** Stepped 0..3 color ladder (shared by score_bank + iwh_meter). */
export const stepColors: readonly string[] = [
  palette.boneFaint, // 0 — no signal
  palette.boneDim, // 1
  palette.phosphorDim, // 2
  palette.phosphor, // 3
];

export type PaletteKey = keyof typeof palette;
