"""Development environment doctor for X Growth Dashboard.

Usage:
    uv run python scripts/doctor.py

Verifies the local toolchain required by this repo. Never prints secret values.
Exit 0 when all required checks pass; 1 when any required check fails.
Optional tools may warn without failing the run.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Ensure project root is importable when invoked as `python scripts/doctor.py`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.paths import (  # noqa: E402
    LEGACY_DATA_DIR,
    application_support_dir,
    resolve_data_dir,
    resolve_db_path,
)

Status = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


def check_uv() -> CheckResult:
    uv = shutil.which("uv")
    if not uv:
        return CheckResult(
            "uv",
            "fail",
            "uv not found on PATH. Install: curl -LsSf https://astral.sh/uv/install.sh | sh",
        )
    proc = _run([uv, "--version"])
    version = proc.stdout.strip() or proc.stderr.strip() or "unknown version"
    if proc.returncode != 0:
        return CheckResult("uv", "fail", f"uv present but failed to report version: {version}")
    return CheckResult("uv", "ok", version)


def _python_version_label(version: object) -> str:
    return f"{version.major}.{version.minor}.{version.micro}"  # type: ignore[attr-defined]


def python_version_ok(version: object) -> bool:
    return (version.major, version.minor) >= (3, 11)  # type: ignore[attr-defined]


def check_python() -> CheckResult:
    version = sys.version_info
    label = _python_version_label(version)
    if not python_version_ok(version):
        return CheckResult(
            "python",
            "fail",
            f"Python {label} is below the required >= 3.11",
        )
    return CheckResult("python", "ok", f"Python {label}")


def check_venv(root: Path = ROOT) -> CheckResult:
    venv = root / ".venv"
    if not venv.is_dir():
        return CheckResult(
            ".venv",
            "fail",
            ".venv missing. Run: uv sync",
        )
    missing: list[str] = []
    for exe in ("python", "pytest", "ruff"):
        path = venv / "bin" / exe
        if not path.exists():
            missing.append(str(path))
    if missing:
        return CheckResult(
            ".venv",
            "fail",
            f".venv exists but missing executables: {', '.join(missing)}",
        )
    return CheckResult(".venv", "ok", f"Provisioned at {venv}")


def detect_node_package_manager(root: Path = ROOT) -> str | None:
    desktop = root / "desktop"
    if (desktop / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (desktop / "package-lock.json").exists():
        return "npm"
    if (desktop / "yarn.lock").exists():
        return "yarn"
    return None


def check_desktop_node(root: Path = ROOT) -> CheckResult:
    desktop = root / "desktop"
    package_json = desktop / "package.json"
    if not package_json.is_file():
        return CheckResult("desktop/package.json", "fail", "desktop/package.json not found")
    pm = detect_node_package_manager(root)
    if pm is None:
        return CheckResult(
            "desktop lockfile",
            "warn",
            "desktop/package.json exists but no pnpm/npm/yarn lockfile was found",
        )
    pm_bin = shutil.which(pm)
    if pm_bin is None:
        return CheckResult(
            pm,
            "fail",
            f"{pm} not found on PATH (lockfile indicates {pm})",
        )
    proc = _run([pm_bin, "--version"])
    version = proc.stdout.strip() or proc.stderr.strip() or "unknown version"
    if proc.returncode != 0:
        return CheckResult(pm, "fail", f"{pm} failed to report version: {version}")
    return CheckResult(
        "desktop node toolchain",
        "ok",
        f"{pm} {version}; lockfile={pm}-lock detected",
    )


def check_rust_tauri(root: Path = ROOT) -> CheckResult:
    cargo_toml = root / "desktop" / "src-tauri" / "Cargo.toml"
    if not cargo_toml.is_file():
        return CheckResult("tauri", "fail", f"Missing {cargo_toml}")
    cargo = shutil.which("cargo")
    rustc = shutil.which("rustc")
    if not cargo or not rustc:
        missing = []
        if not cargo:
            missing.append("cargo")
        if not rustc:
            missing.append("rustc")
        return CheckResult(
            "rust",
            "warn",
            f"{' and '.join(missing)} not found on PATH (required for native desktop builds)",
        )
    cargo_proc = _run([cargo, "--version"])
    rustc_proc = _run([rustc, "--version"])
    cargo_ver = cargo_proc.stdout.strip() or "unknown cargo"
    rustc_ver = rustc_proc.stdout.strip() or "unknown rustc"
    if cargo_proc.returncode != 0 or rustc_proc.returncode != 0:
        return CheckResult("rust", "fail", "Rust toolchain present but version check failed")
    return CheckResult("rust", "ok", f"{cargo_ver}; {rustc_ver}; {cargo_toml.name} present")


def check_sqlite() -> CheckResult:
    sqlite3_bin = shutil.which("sqlite3")
    if sqlite3_bin:
        proc = _run([sqlite3_bin, "--version"])
        version = proc.stdout.strip() or proc.stderr.strip() or "unknown version"
        if proc.returncode == 0:
            return CheckResult("sqlite3", "ok", version)
    try:
        import sqlite3

        sqlite3.connect(":memory:").close()
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("sqlite3", "fail", f"SQLite unavailable: {exc}")
    return CheckResult("sqlite3", "ok", "Python sqlite3 module available")


def check_keyring() -> CheckResult:
    if importlib.util.find_spec("keyring") is None:
        return CheckResult(
            "keyring",
            "warn",
            "keyring package not importable (native secret storage may be unavailable)",
        )
    return CheckResult("keyring", "ok", "keyring importable")


def check_env_files(root: Path = ROOT) -> CheckResult:
    example = root / ".env.example"
    env_file = root / ".env"
    parts: list[str] = []
    if example.is_file():
        parts.append(".env.example present")
    else:
        parts.append(".env.example missing")
    if env_file.is_file():
        parts.append(".env present (values not shown)")
    else:
        parts.append(".env missing (optional for tests; required for live agent/API features)")
    status: Status = "ok" if example.is_file() else "warn"
    return CheckResult("env files", status, "; ".join(parts))


def check_data_paths() -> CheckResult:
    active_dir = resolve_data_dir()
    db_path = resolve_db_path()
    if os.environ.get("XGROWTH_DATA_DIR"):
        source = "XGROWTH_DATA_DIR override"
    elif (application_support_dir() / "dashboard.db").exists():
        source = "Application Support"
    elif LEGACY_DATA_DIR.exists():
        source = "legacy ./data"
    else:
        source = "legacy ./data (not yet created)"
    return CheckResult(
        "data paths",
        "ok",
        f"active data dir={active_dir}; db={db_path}; source={source}",
    )


def run_checks(root: Path = ROOT) -> list[CheckResult]:
    return [
        check_uv(),
        check_python(),
        check_venv(root),
        check_desktop_node(root),
        check_rust_tauri(root),
        check_sqlite(),
        check_keyring(),
        check_env_files(root),
        check_data_paths(),
    ]


def format_report(results: list[CheckResult]) -> str:
    lines = ["XGrowthDashboard environment doctor", ""]
    for result in results:
        prefix = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}[result.status]
        lines.append(f"[{prefix}] {result.name}: {result.message}")
    lines.append("")
    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warn")
    if failed:
        lines.append(f"Result: {failed} required check(s) failed, {warned} warning(s).")
    elif warned:
        lines.append(f"Result: all required checks passed with {warned} warning(s).")
    else:
        lines.append("Result: all checks passed.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _ = argv
    results = run_checks()
    print(format_report(results))
    if any(result.status == "fail" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
