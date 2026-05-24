#!/usr/bin/env bash
# Build the frozen Python sidecar for the native macOS app (spec §31.6).
#
# Produces a single-file `xgrowth-sidecar` executable at
# desktop/src-tauri/bin/ which Tauri bundles into the .app's Resources
# (see tauri.conf.json bundle.resources). The binary embeds the read-only
# resources (migrations/, config/, spec.md); Streamlit is excluded.
#
# Run from the repo root:
#   ./scripts/build_sidecar.sh
#
# Then build the .app:
#   pnpm -C desktop tauri build
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "› Freezing the FastAPI sidecar with PyInstaller…"
uv run pyinstaller xgrowth_sidecar.spec \
  --distpath desktop/src-tauri/bin \
  --workpath build/pyinstaller \
  --noconfirm

BIN="desktop/src-tauri/bin/xgrowth-sidecar"
if [[ -x "$BIN" ]]; then
  echo "✓ Sidecar built: $BIN ($(du -h "$BIN" | cut -f1))"
else
  echo "✗ Sidecar binary missing at $BIN" >&2
  exit 1
fi
