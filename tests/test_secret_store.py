"""Phase 11.1 tests for Keychain-backed secret resolution (§31.5)."""

from __future__ import annotations

import sys
import types

import pytest

from app import secret_store


def test_resolve_secret_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert secret_store.resolve_anthropic_api_key() == "env-key"


def test_resolve_secret_falls_back_to_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        secret_store,
        "_keyring_get",
        lambda name: "kc-key" if name == "ANTHROPIC_API_KEY" else None,
    )
    assert secret_store.resolve_anthropic_api_key() == "kc-key"


def test_resolve_secret_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(secret_store, "_keyring_get", lambda name: None)
    assert secret_store.resolve_anthropic_api_key() is None


def test_keyring_get_returns_none_on_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.ModuleType("keyring")

    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("no keyring backend available")

    fake.get_password = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert secret_store._keyring_get("ANTHROPIC_API_KEY") is None


def test_store_secret_calls_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    fake = types.ModuleType("keyring")
    fake.set_password = lambda service, name, value: captured.update(  # type: ignore[attr-defined]
        service=service, name=name, value=value
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    secret_store.store_secret("ANTHROPIC_API_KEY", "abc")
    assert captured == {
        "service": secret_store.KEYCHAIN_SERVICE,
        "name": "ANTHROPIC_API_KEY",
        "value": "abc",
    }
