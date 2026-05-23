"""Re-record Phase 8 X API fixture cassettes.

Run this script when X API contracts change (response shape, status
codes, error envelope keys). It posts real tweets to the authenticated
X account, captures the responses into YAML cassettes under
``tests/fixtures/x_api/``, then auto-deletes every tweet it posted.

See ``docs/X_API_FIXTURES.md`` for the full procedure, the cassette
format, and the safety rules.

Usage::

    uv run python -m scripts.rerecord_x_api_fixtures             # interactive
    uv run python -m scripts.rerecord_x_api_fixtures --no-prompt # CI / scripted
    uv run python -m scripts.rerecord_x_api_fixtures --dry-run   # print plan
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import yaml

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "x_api"


def _stamp_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_RECORDABLE_CASSETTES: list[tuple[str, dict[str, object]]] = [
    (
        "publish_post_success_200",
        {
            "method": "POST",
            "endpoint": "/2/tweets",
            "body_template": lambda last_id: {
                "text": "(scalinity.ai X API fixture re-record — auto-delete) "
                + _stamp_iso(),
            },
        },
    ),
    (
        "publish_reply_success_200",
        {
            "method": "POST",
            "endpoint": "/2/tweets",
            "body_template": lambda last_id: {
                "text": "(scalinity.ai X API fixture re-record — reply auto-delete) "
                + _stamp_iso(),
                "reply": {"in_reply_to_tweet_id": last_id} if last_id else None,
            },
            "needs_reply_id_from": "publish_post_success_200",
        },
    ),
    (
        "recent_tweets_match",
        {
            "method": "GET",
            "endpoint": "/2/users/me/tweets?max_results=10",
            "body_template": lambda last_id: None,
        },
    ),
]


def _ensure_xurl() -> None:
    if shutil.which("xurl") is None:
        sys.exit(
            "ERROR: xurl is not installed. See docs/X_API_SETUP.md §1 to install."
        )
    proc = subprocess.run(
        ["xurl", "/2/users/me"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0 or "errors" in (proc.stdout or ""):
        sys.exit(
            "ERROR: xurl auth check failed. Re-run `xurl auth login` "
            "with `tweet.read tweet.write users.read offline.access` scope. "
            "See docs/X_API_SETUP.md §8."
        )


def _confirm_or_exit(no_prompt: bool) -> None:
    if no_prompt:
        return
    print(
        dedent(
            """
            ============================================================
            About to post REAL tweets to your authenticated X account
            to re-record Phase 8 fixture cassettes.

            Each posted tweet is auto-deleted via DELETE /2/tweets/{id}
            before this script exits. A failed delete is a script-level
            FAILURE — you'll need to manually delete the orphan from
            your X timeline.

            See docs/X_API_FIXTURES.md §5 for the safety rules.
            ============================================================
            """
        )
    )
    answer = input("Type 'yes' to proceed: ").strip().lower()
    if answer != "yes":
        sys.exit("Aborted at user prompt.")


def _xurl(method: str, endpoint: str, body: dict[str, object] | None) -> tuple[int, str, str]:
    argv: list[str] = ["xurl"]
    if method != "GET":
        argv += ["--request", method]
    if body is not None:
        argv += ["--data", json.dumps(body)]
    argv.append(endpoint)
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _record(
    plan: list[tuple[str, dict[str, object]]],
    posted_ids: list[str] | None = None,
) -> list[str]:
    """Record cassettes; append posted X IDs to ``posted_ids`` as we go.

    P8R-18: ``posted_ids`` can be supplied by the caller as a
    pre-allocated list so the main() can run cleanup in a try/finally
    even if _record raises partway through. The caller's list is
    populated INCREMENTALLY (per-cassette) — a crash after cassette
    N posts but before cassette N+1 still leaves the caller with the
    N IDs to clean up.
    """
    if posted_ids is None:
        posted_ids = []
    last_post_id: str | None = None
    for name, shape in plan:
        method = str(shape["method"])
        endpoint = str(shape["endpoint"])
        body_template = shape["body_template"]
        assert callable(body_template)
        body_raw = body_template(last_post_id)
        if isinstance(body_raw, dict):
            body = {k: v for k, v in body_raw.items() if v is not None}
        else:
            body = body_raw  # type: ignore[assignment]

        exit_code, stdout, stderr = _xurl(method, endpoint, body)  # type: ignore[arg-type]
        try:
            response_body = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            print(f"  ! {name}: xurl returned non-JSON; skipping cassette write.")
            continue

        cassette = {
            "interactions": [
                {
                    "request": {
                        "method": method,
                        "uri": endpoint,
                        "body": body or None,
                    },
                    "response": {
                        "status_code": _infer_status(response_body),
                        "body": response_body,
                        "stderr": stderr,
                        "exit_code": exit_code,
                    },
                }
            ]
        }
        out_path = _FIXTURES_DIR / f"{name}.yaml"
        with out_path.open("w", encoding="utf-8") as fp:
            yaml.safe_dump(cassette, fp, sort_keys=False)
        print(f"  + wrote {out_path.relative_to(_FIXTURES_DIR.parent.parent)}")

        if method == "POST" and isinstance(response_body, dict):
            data = response_body.get("data")
            if isinstance(data, dict):
                new_id = data.get("id")
                if isinstance(new_id, str):
                    posted_ids.append(new_id)
                    last_post_id = new_id
        time.sleep(1.5)
    return posted_ids


def _delete_posted(ids: list[str]) -> int:
    failures = 0
    for x_id in ids:
        exit_code, stdout, stderr = _xurl("DELETE", f"/2/tweets/{x_id}", None)
        if exit_code != 0 or "errors" in (stdout or ""):
            print(f"  ! FAILED to delete {x_id}: stderr={stderr.strip()[:200]!r}")
            failures += 1
        else:
            print(f"  - deleted {x_id}")
    return failures


def _infer_status(body: object) -> int:
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            status = errors[0].get("status")
            if isinstance(status, int):
                return status
        if "data" in body or "meta" in body:
            return 200
    return 200


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-record Phase 8 X API fixture cassettes.")
    parser.add_argument("--no-prompt", action="store_true", help="Skip interactive confirmation.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan; do not call xurl.")
    args = parser.parse_args(argv)

    if args.dry_run:
        print("Plan:")
        for name, shape in _RECORDABLE_CASSETTES:
            print(f"  {name}: {shape['method']} {shape['endpoint']}")
        return 0

    _ensure_xurl()
    _confirm_or_exit(no_prompt=args.no_prompt)

    print("Recording cassettes...")
    # P8R-18: pre-allocate the posted-ids list so cleanup runs even if
    # _record() raises partway through. Without this, a crash after
    # cassette N posted (and before N+1) would leak the N already-
    # posted tweets on Daniel's timeline.
    posted: list[str] = []
    try:
        _record(_RECORDABLE_CASSETTES, posted_ids=posted)
    finally:
        if posted:
            print(f"\nCleaning up {len(posted)} posted tweets...")
            failures = _delete_posted(posted)
            if failures:
                print(
                    f"\nERROR: {failures} of {len(posted)} delete calls failed. "
                    "Manually remove the orphaned tweets from your X timeline."
                )
                # Re-raise via non-zero exit even if _record succeeded —
                # orphaned tweets are a script-level failure regardless.
                return 1
        else:
            print("No real posts created; nothing to clean up.")
    print("\nDone. Verify `git diff tests/fixtures/x_api/` before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
