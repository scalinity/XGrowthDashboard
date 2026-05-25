"""Regression guard: the FastAPI sidecar must import without Streamlit (§31.6).

The frozen PyInstaller bundle (scripts/build_sidecar.sh) excludes Streamlit to
keep the binary small. Several `app.components.*` / `app.forms.*` modules mix
pure logic (which the sidecar imports) with Streamlit `render()` functions; if
any of them does a module-level `import streamlit`, the frozen sidecar crashes
at import time — before printing its handshake — and the native app hangs on
"loading".

The ordinary smoke tests can't catch this: pytest runs in the dev venv where
Streamlit *is* installed, so `import app.service.app` always succeeds. This test
reproduces the frozen condition in an isolated subprocess that makes
`import streamlit` raise ModuleNotFoundError, then imports the sidecar and runs
its app factory. If a future change reintroduces an eager Streamlit import into
the sidecar's import graph, this fails loudly instead of only at `tauri build`.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Run in a subprocess so the import graph is built fresh with Streamlit blocked,
# independent of whatever the rest of the suite has already imported.
_PROBE = textwrap.dedent(
    """
    import sqlite3
    import sys
    import importlib.abc

    class _BlockStreamlit(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name == "streamlit" or name.startswith("streamlit."):
                raise ModuleNotFoundError(f"No module named '{name}'")
            return None

    sys.meta_path.insert(0, _BlockStreamlit())

    import app.service.app as svc

    app = svc.create_app(token="probe", conn_factory=lambda: sqlite3.connect(":memory:"))
    assert app.title.startswith("X Growth Dashboard"), app.title
    print("SIDECAR_IMPORT_OK")
    """
)


def test_sidecar_imports_and_builds_app_without_streamlit() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Sidecar failed to import with Streamlit unavailable — a module in its "
        "import graph does a module-level `import streamlit`. Route it through "
        "`app._optional_streamlit` instead (§31.6).\n\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "SIDECAR_IMPORT_OK" in result.stdout, result.stdout
