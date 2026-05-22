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
