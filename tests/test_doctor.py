"""Tests for scripts/doctor.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import doctor


def test_detect_node_package_manager_prefers_pnpm(tmp_path: Path) -> None:
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    (desktop / "package.json").write_text("{}", encoding="utf-8")
    (desktop / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert doctor.detect_node_package_manager(tmp_path) == "pnpm"


def test_python_version_ok() -> None:
    assert doctor.python_version_ok(SimpleNamespace(major=3, minor=11, micro=0))
    assert not doctor.python_version_ok(SimpleNamespace(major=3, minor=10, micro=12))


def test_check_venv_reports_missing_executables(tmp_path: Path) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("", encoding="utf-8")
    result = doctor.check_venv(tmp_path)
    assert result.status == "fail"
    assert "pytest" in result.message


def test_check_env_files_never_prints_secret_values(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=example\n", encoding="utf-8")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=super-secret\n", encoding="utf-8")
    result = doctor.check_env_files(tmp_path)
    assert "super-secret" not in result.message
    assert "values not shown" in result.message


def test_format_report_counts_failures() -> None:
    report = doctor.format_report(
        [
            doctor.CheckResult("uv", "ok", "uv 0.6.0"),
            doctor.CheckResult("rust", "warn", "cargo missing"),
            doctor.CheckResult(".venv", "fail", "missing"),
        ]
    )
    assert "FAIL" in report
    assert "1 required check(s) failed" in report


def test_main_returns_nonzero_when_required_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor,
        "run_checks",
        lambda root=doctor.ROOT: [doctor.CheckResult("uv", "fail", "missing")],
    )
    assert doctor.main() == 1
