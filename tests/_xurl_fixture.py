"""Subprocess-aware fixture loader for Phase 8 vcr.py-compatible cassettes.

`app/x_client.py` shells out to the `xurl` CLI via `subprocess.run`.
vcr.py instruments Python HTTP libraries; it doesn't see subprocess
calls. This module reads vcr.py-shaped YAML cassettes under
`tests/fixtures/x_api/` and patches `subprocess.run` so a matching
request returns the canned response without invoking the real `xurl`
binary.

The cassette YAML shape is identical to vcr.py's, so a future
transport migration to native Python HTTP (requests/httpx) can play
back the same files without rewriting.

Usage in tests::

    from tests._xurl_fixture import use_cassette

    def test_publish_succeeds(monkeypatch, db_conn):
        with use_cassette(monkeypatch, "publish_post_success_200"):
            result = publish_post_to_x(db_conn, post_id=42, confirmation_token=tok)
        assert result.success
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Reuse the project's standard fixtures directory.
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "x_api"


@dataclass(frozen=True)
class _CassetteInteraction:
    """One request/response pair loaded from a cassette."""

    method: str
    uri: str
    response_status_code: int | None
    response_body: dict[str, Any] | list[Any] | None
    response_stderr: str
    response_exit_code: int
    response_raise: str | None
    response_raise_timeout: float | None


def _load_cassette(name: str) -> list[_CassetteInteraction]:
    path = _FIXTURES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"x_api fixture cassette not found: {path}. "
            f"Available: {sorted(p.stem for p in _FIXTURES_DIR.glob('*.yaml'))}"
        )
    with path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}
    out: list[_CassetteInteraction] = []
    for interaction in raw.get("interactions", []):
        req = interaction.get("request", {}) or {}
        resp = interaction.get("response", {}) or {}
        out.append(
            _CassetteInteraction(
                method=(req.get("method") or "GET").upper(),
                uri=req.get("uri") or "",
                response_status_code=resp.get("status_code"),
                response_body=resp.get("body"),
                response_stderr=resp.get("stderr") or "",
                response_exit_code=int(resp.get("exit_code", 0)),
                response_raise=resp.get("raise"),
                response_raise_timeout=resp.get("timeout_seconds"),
            )
        )
    return out


def _normalize_endpoint(endpoint_arg: str) -> tuple[str, str]:
    """Split an endpoint into (path, query_string) for prefix matching."""
    if "?" in endpoint_arg:
        path, _, query = endpoint_arg.partition("?")
    else:
        path, query = endpoint_arg, ""
    return path, query


def _matches(interaction: _CassetteInteraction, argv: list[str]) -> bool:
    """True iff the cassette interaction matches the xurl argv shape."""
    method = "GET"
    endpoint_arg = ""
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--request" and i + 1 < len(argv):
            method = argv[i + 1].upper()
            i += 2
            continue
        if token == "--data" and i + 1 < len(argv):
            i += 2
            continue
        endpoint_arg = token
        i += 1
    if interaction.method != method:
        return False
    cassette_path, _ = _normalize_endpoint(interaction.uri)
    arg_path, _ = _normalize_endpoint(endpoint_arg)
    if not arg_path.startswith(cassette_path):
        return False
    return True


def _build_completed_process(
    argv: list[str], interaction: _CassetteInteraction
) -> subprocess.CompletedProcess[str]:
    body_obj = interaction.response_body
    if body_obj is None:
        stdout = ""
    else:
        body_text = json.dumps(_substitute_echo(body_obj, argv))
        stdout = body_text
    return subprocess.CompletedProcess(
        args=argv,
        returncode=interaction.response_exit_code,
        stdout=stdout,
        stderr=interaction.response_stderr,
    )


def _substitute_echo(
    body: dict[str, Any] | list[Any], argv: list[str]
) -> dict[str, Any] | list[Any]:
    """Replace the string sentinel "ECHO" with the request's text payload.

    Cassettes use "ECHO" so the success response surfaces the same text
    Daniel wrote. Pulled from --data JSON in argv.
    """
    text: str | None = None
    for i, token in enumerate(argv):
        if token == "--data" and i + 1 < len(argv):
            try:
                data = json.loads(argv[i + 1])
            except (TypeError, ValueError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict):
                text = data.get("text")
            break
    if text is None:
        return body

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if node == "ECHO":
            return text
        return node

    return _walk(body)


@contextmanager
def use_cassette(
    monkeypatch: Any, names: str | list[str], *, assert_all_played: bool = True
) -> Iterator[list[list[str]]]:
    """Patch subprocess.run to serve responses from the named cassettes.

    Yields the recorded argv list for each subprocess.run call.

    Raises AssertionError on exit if ``assert_all_played`` is True and
    one or more cassettes were not consumed.
    """
    if isinstance(names, str):
        names = [names]
    queues: list[list[_CassetteInteraction]] = [
        list(_load_cassette(n)) for n in names
    ]
    recorded_calls: list[list[str]] = []

    def _fake_run(argv: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded_calls.append(list(argv))
        for queue in queues:
            if not queue:
                continue
            interaction = queue[0]
            if _matches(interaction, argv):
                queue.pop(0)
                if interaction.response_raise == "subprocess.TimeoutExpired":
                    raise subprocess.TimeoutExpired(
                        cmd=argv,
                        timeout=interaction.response_raise_timeout or 30.0,
                    )
                return _build_completed_process(argv, interaction)
        raise AssertionError(
            "No matching x_api cassette interaction for argv: "
            f"{argv!r}. Loaded cassettes (in order): {names}. "
            f"Remaining queues: {[len(q) for q in queues]}"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    try:
        yield recorded_calls
    finally:
        if assert_all_played:
            unplayed = [
                f"{name}[{len(q)}]"
                for name, q in zip(names, queues)
                if q
            ]
            if unplayed:
                raise AssertionError(
                    "x_api cassettes had unplayed interactions "
                    "(no matching subprocess.run call fired): "
                    f"{unplayed}"
                )


@contextmanager
def assert_no_x_api_calls(monkeypatch: Any) -> Iterator[None]:
    """Fail the test if subprocess.run is called with the xurl binary."""
    real_run = subprocess.run

    def _guard(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        binary = argv[0] if argv else ""
        if "xurl" in str(binary):
            raise AssertionError(
                "X API call fired during manual-fallback test: "
                f"argv={argv!r}"
            )
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guard)
    yield


def available_cassettes() -> list[str]:
    """List of stems under tests/fixtures/x_api/ for ergonomic test setup."""
    return sorted(p.stem for p in _FIXTURES_DIR.glob("*.yaml"))
