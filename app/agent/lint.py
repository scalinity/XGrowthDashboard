"""Dark-pattern + voice lint pass (§28.2 rule #12).

Every draft passes through ``lint_draft`` BEFORE ``save_draft_post`` /
``save_draft_reply`` persists it. A blocked draft counts as a failed
IWH revision attempt (§28.2 rule #13) and is bounced back to the agent
for a rewrite. The agent cannot disable the lint pass — this module
lives outside the agent loop.

The model used is Claude Haiku (cheap, fast, sufficient for one-shot
"yes/no with one-line reasoning"). Cost per call is rounding error
against the §28.6 monthly cap.

Offline test mode: set ``LINT_OFFLINE=1`` (env var) to skip the Anthropic
call entirely and use a deterministic substring matcher. Tests rely on
this; it lets the dark-pattern test from §25 run without an API key.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Offline-mode pattern matchers — used by tests and by the safety fallback
# when the Anthropic API is unreachable.
# ---------------------------------------------------------------------------
_ENGAGEMENT_BAIT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bnumber\s+\d+\s+will\s+(surprise|shock|amaze)", "engagement-bait: 'number N will surprise you'"),
    (r"\b\d+\s+secrets?\b.*\b(don't|do not)\s+know\b", "engagement-bait: '5 secrets X don't know' framing"),
    (r"\byou\s+won't\s+believe\b", "engagement-bait: 'you won't believe'"),
    (r"\bthis\s+one\s+(weird\s+)?trick\b", "engagement-bait: 'this one trick'"),
    (r"\b(only|just)\s+\d+\s+(spots?|seats?)\s+left\b", "fake scarcity: 'only N spots left'"),
    (r"\b(act|hurry|order)\s+now!?\s*$", "fake urgency: 'act now / hurry now'"),
    (r"\bdon't\s+miss\s+out\b", "FOMO without basis: 'don't miss out'"),
    (r"\b(everyone|everybody)\s+is\s+(talking|saying)\b", "fabricated social proof: 'everyone is talking'"),
)


@dataclass(frozen=True)
class LintResult:
    """Output of the lint pass — passed up to the IWH counter for decision."""

    dark_pattern_detected: bool
    rationale: str
    voice_match_score: float | None = None  # 0.0-1.0; None if not computed
    specific_issues: list[str] = field(default_factory=list)
    model_used: str | None = None  # haiku model id, or 'offline' for pattern match
    # W14: explicit field so audit reviewers can detect intermittent API
    # outages instead of inferring from prose. Set to True when the live
    # Haiku call failed and we fell back to the offline pattern matcher.
    api_call_failed: bool = False


def _offline_lint(text: str) -> LintResult:
    """Deterministic pattern matcher — used in tests and as API fallback."""
    issues: list[str] = []
    for pat, label in _ENGAGEMENT_BAIT_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            issues.append(label)
    detected = bool(issues)
    rationale = (
        f"offline lint matched {len(issues)} engagement-bait pattern(s)"
        if detected
        else "offline lint: no engagement-bait patterns matched"
    )
    return LintResult(
        dark_pattern_detected=detected,
        rationale=rationale,
        specific_issues=issues,
        model_used="offline",
    )


# S10: the dark-pattern definition is now spliced from spec.md §28.2 rule
# #12 so the Haiku gate and the main agent's training share one source of
# truth. The prose between BEGIN/END is the verbatim spec rule body; the
# Haiku call wraps it in the JSON-output framing.
_DARK_PATTERN_DEFINITION = """\
DARK PATTERNS ARE FORBIDDEN. The draft must NOT use any of:
- fake urgency
- manufactured scarcity
- fabricated social proof ('many founders are saying', 'everyone is talking')
- engagement bait that doesn't deliver on its hook (curiosity gap that the
  post never closes, '5 secrets X don't know — number 3 will...')
- 'controversial takes' engineered for arguments
- manipulation of insecurity, fear, or FOMO without basis
- pattern interrupts used to engineer outrage
- inflated credentials, role-inflation, fabricated authority

Engagement principles are TOOLS for clarity, not levers for manipulation:
specificity, curiosity gaps the post pays off, real social proof citing
actual numbers, real scarcity citing actual constraints. If the post
cannot link a claim to a verifiable basis, the claim is forbidden."""

_HAIKU_LINT_PROMPT = """You are a content-safety lint pass for an X (Twitter) draft.

""" + _DARK_PATTERN_DEFINITION + """

Read the draft below. Decide: does it use any of the forbidden patterns?

Reply STRICTLY as JSON with these keys:
  dark_pattern_detected: true | false
  rationale: one-line reason
  specific_issues: list of short strings (empty if no issues)

Do not include any text outside the JSON object.

Draft:
{draft}
"""


def lint_draft(
    text: str,
    *,
    voice_samples_text: list[str] | None = None,  # noqa: ARG001 — Session 2+ voice scoring
    model: str = "claude-haiku-4-5",
) -> LintResult:
    """Run the dark-pattern lint pass over ``text``. Returns ``LintResult``.

    Honors ``LINT_OFFLINE=1`` env var: skips the API and runs the offline
    pattern matcher only. Production code should leave the var unset; tests
    set it.
    """
    if os.environ.get("LINT_OFFLINE") == "1":
        return _offline_lint(text)

    # Live Anthropic call. We import here so the offline path doesn't even
    # need the package installed at module-import time.
    try:
        import anthropic
    except ImportError:
        # Anthropic SDK unavailable — fall back to offline pattern match.
        return _offline_lint(text)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _offline_lint(text)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": _HAIKU_LINT_PROMPT.format(draft=text),
                }
            ],
        )
        # Extract first text block.
        body = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                body = block.text
                break
        if not body:
            return _offline_lint(text)
        # Strict JSON expected; tolerate a leading code-fence.
        body = body.strip()
        if body.startswith("```"):
            body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body, flags=re.DOTALL)
        data = json.loads(body)
        return LintResult(
            dark_pattern_detected=bool(data.get("dark_pattern_detected", False)),
            rationale=str(data.get("rationale", "")),
            specific_issues=[str(s) for s in data.get("specific_issues", [])],
            model_used=model,
        )
    except Exception as exc:
        # W13: broaden the catch — httpx.ConnectError / httpx.TimeoutException
        # / unforeseen SDK errors were escaping the narrow tuple and
        # crashing decide_save_or_revise. The lint pass is a safety net;
        # falling silent to the offline matcher is strictly better than
        # surfacing a Streamlit traceback for a transient network blip.
        #
        # W14: api_call_failed=True + a Plain rationale ("offline fallback
        # — Haiku unreachable") so audit reviewers can grep / filter for
        # intermittent outages instead of inferring from a prose prefix.
        result = _offline_lint(text)
        return LintResult(
            dark_pattern_detected=result.dark_pattern_detected,
            rationale=(
                f"offline-fallback (haiku unreachable: {type(exc).__name__}). "
                f"offline result: {result.rationale}"
            ),
            specific_issues=result.specific_issues,
            model_used="offline-fallback",
            api_call_failed=True,
        )


# ===========================================================================
# Phase 5.9 / §28.18 — reply-quality lint.
# ===========================================================================
# Catches the specific failure mode the source video calls out: forced,
# AI-tasting, or selfishly self-promoting replies. Runs AFTER the dark-
# pattern lint (rule #12) and BEFORE the pre-publish scorer (§28.11) in
# the reply pipeline. Same enforcement contract as dark-pattern lint —
# failure counts as a failed IWH revision attempt.
#
# Gated by `reply_quality_lint_enabled` setting (default true). When
# disabled the lint short-circuits to (True, 'lint disabled') so the
# audit row records that the lint did not run.

# Offline-mode patterns for tests + fallback. These mirror the three
# Haiku failure-mode labels: forced / AI-tasting / selfishly self-
# promoting. Conservative — false positives bounce as IWH revisions;
# false negatives are caught by §28.13 repetition guard + Daniel's eye.
_REPLY_QUALITY_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\bgreat\s+(post|thread|take)!?\s*[\U0001F300-\U0001FAFF🔥👏🙌💯❤️🎉✨].*\b(check|stop\s+by|visit|see)\b",
        "selfishly self-promoting: 'great post! check out my…'",
    ),
    (
        r"\b(check\s+out|stop\s+by|visit|see)\s+(my|our)\s+(stuff|site|product|profile|page)\b",
        "selfishly self-promoting: explicit self-link CTA in a reply",
    ),
    (
        r"\b(amazing|incredible|love\s+this|fire|absolute\s+banger)!?\s*[\U0001F300-\U0001FAFF🔥👏🙌💯❤️🎉✨]+\s*$",
        "forced: emoji-led affirmation with no substantive content",
    ),
    (
        r"^\s*(this|that|so\s+true|exactly|💯|🔥)\.?\s*$",
        "forced: single-word affirmation with no substance",
    ),
    (
        r"\b(as\s+an\s+ai|i\s+am\s+an\s+ai|let\s+me\s+know\s+if\s+you'd\s+like\s+me\s+to)\b",
        "AI-tasting: explicit LLM-template phrasing",
    ),
)


@dataclass(frozen=True)
class ReplyQualityResult:
    """Output of the §28.18 reply-quality lint pass.

    ``passed`` is True when the reply reads as genuine + substantive.
    False bounces back as a failed IWH revision via the same enforcement
    path as ``LintResult.dark_pattern_detected``.

    ``failure_mode`` is one of: 'forced', 'ai_tasting',
    'selfishly_self_promoting', 'lint_disabled', or None on pass.
    """

    passed: bool
    rationale: str
    failure_mode: str | None = None
    model_used: str | None = None
    api_call_failed: bool = False


_REPLY_QUALITY_PROMPT = """You are reviewing a reply to an X post. Does this reply sound forced,
AI-generated, or selfishly self-promoting (would the original poster
find it annoying)?

Target post:
{target_post}

Proposed reply:
{reply}

Reply with exactly one of:
- "no, this is genuine and substantive" + one-line reasoning
- "yes, forced" + one-line reasoning
- "yes, AI-tasting" + one-line reasoning
- "yes, selfishly self-promoting" + one-line reasoning
"""


def _parse_reply_quality_response(
    body: str, *, reply_text: str | None = None
) -> ReplyQualityResult:
    """Map the four-option Haiku response to a ReplyQualityResult.

    Tolerates leading whitespace and the model occasionally wrapping
    its answer in a code fence; the four expected verdict prefixes are
    matched case-insensitively.

    P59A-W3: when the response is unparseable, fall back to the offline
    pattern matcher (symmetric with the dark-pattern lint's outage
    fallback). Soft-passing was an asymmetry that quietly created a
    §28.18 enforcement gap: a Haiku that emitted 'unsure — could be
    selfish' (no expected verdict prefix) was treated as a pass.
    """
    text = body.strip().lower()
    if text.startswith("no,") or text.startswith("no:") or text.startswith("no "):
        return ReplyQualityResult(
            passed=True,
            rationale=body.strip(),
            failure_mode=None,
        )
    if "forced" in text and text.startswith("yes"):
        return ReplyQualityResult(
            passed=False, rationale=body.strip(), failure_mode="forced"
        )
    if "ai-tasting" in text or "ai tasting" in text:
        return ReplyQualityResult(
            passed=False, rationale=body.strip(), failure_mode="ai_tasting"
        )
    if "self-promoting" in text or "selfishly" in text:
        return ReplyQualityResult(
            passed=False,
            rationale=body.strip(),
            failure_mode="selfishly_self_promoting",
        )
    # Unparseable — route through the offline matcher so the dark-pattern
    # and reply-quality lints have symmetric outage contracts. The
    # caller's reply_text is needed for the offline scan; if not
    # provided (legacy call sites) we still default-pass with the
    # original rationale so we don't regress those.
    if reply_text is not None:
        offline = _offline_reply_quality(reply_text)
        return ReplyQualityResult(
            passed=offline.passed,
            rationale=(
                f"unparseable haiku response → offline fallback: "
                f"{offline.rationale}. raw: {body[:200]!r}"
            ),
            failure_mode=offline.failure_mode,
            model_used="offline-fallback",
        )
    return ReplyQualityResult(
        passed=True,
        rationale=f"unparseable response — defaulted to pass: {body[:200]!r}",
        failure_mode=None,
    )


def _offline_reply_quality(text: str) -> ReplyQualityResult:
    """Deterministic pattern matcher — used in tests + as API fallback."""
    for pat, label in _REPLY_QUALITY_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            mode = (
                "selfishly_self_promoting" if "self-promoting" in label
                else "ai_tasting" if "AI-tasting" in label
                else "forced"
            )
            return ReplyQualityResult(
                passed=False,
                rationale=f"offline reply-quality lint matched: {label}",
                failure_mode=mode,
                model_used="offline",
            )
    return ReplyQualityResult(
        passed=True,
        rationale="offline reply-quality lint: no failure-mode patterns matched",
        failure_mode=None,
        model_used="offline",
    )


def is_reply_quality_lint_enabled(value_json: str | None) -> bool:
    """Parse the ``reply_quality_lint_enabled`` setting value_json."""
    if value_json is None:
        return True
    try:
        return bool(json.loads(value_json))
    except (json.JSONDecodeError, ValueError):
        return True


def reply_quality_lint(
    text: str,
    target_post_text: str | None,
    *,
    model: str = "claude-haiku-4-5",
    enabled: bool = True,
) -> ReplyQualityResult:
    """Run the §28.18 reply-quality lint over ``text``.

    When ``enabled=False`` short-circuits to ``passed=True`` with
    ``failure_mode='lint_disabled'`` and ``rationale='lint disabled'``;
    the audit row carries the disabled state so the trail is complete.

    Honors ``LINT_OFFLINE=1`` env var: skips the Haiku call and runs the
    deterministic pattern matcher only.

    When the model API is unavailable or returns unparseable output, we
    fall back to the offline matcher. The ``api_call_failed`` field
    distinguishes outage from clean offline-mode invocation.
    """
    if not enabled:
        return ReplyQualityResult(
            passed=True,
            rationale="lint disabled",
            failure_mode="lint_disabled",
            model_used="disabled",
        )

    if os.environ.get("LINT_OFFLINE") == "1":
        return _offline_reply_quality(text)

    try:
        import anthropic
    except ImportError:
        return _offline_reply_quality(text)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _offline_reply_quality(text)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": _REPLY_QUALITY_PROMPT.format(
                        target_post=(target_post_text or "(target post not provided)"),
                        reply=text,
                    ),
                }
            ],
        )
        body = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                body = block.text
                break
        if not body:
            return _offline_reply_quality(text)
        parsed = _parse_reply_quality_response(body, reply_text=text)
        return ReplyQualityResult(
            passed=parsed.passed,
            rationale=parsed.rationale,
            failure_mode=parsed.failure_mode,
            model_used=parsed.model_used or model,
        )
    except Exception as exc:
        result = _offline_reply_quality(text)
        return ReplyQualityResult(
            passed=result.passed,
            rationale=(
                f"offline-fallback (haiku unreachable: {type(exc).__name__}). "
                f"offline result: {result.rationale}"
            ),
            failure_mode=result.failure_mode,
            model_used="offline-fallback",
            api_call_failed=True,
        )
