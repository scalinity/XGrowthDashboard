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
    # RV2-26: bound the .* to avoid adversarial-input catastrophic backtracking.
    (r"\b\d+\s+secrets?\b[^.\n]{0,200}\b(don't|do not)\s+know\b", "engagement-bait: '5 secrets X don't know' framing"),
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
#
# P59A-W14+S6: emoji character class is range-only with explicit
# outliers (U+2700–U+27BF dingbats covers ❤️ ✨ etc; U+2764 is the
# bare heart used by ❤️ before variation selector). Prior literal-
# emoji-in-class form was a no-op for most codepoints already inside
# the range and added U+FE0F (variation selector) as a class member
# by accident.
_EMOJI_CLASS = "[\\U0001F300-\\U0001FAFF\\u2700-\\u27BF\\u2764]"
_REPLY_QUALITY_PATTERNS: tuple[tuple[str, str], ...] = (
    # ---- Phase 5.9 original three categories ----
    (
        # RV2-26: bounded character class instead of .* — adversarial inputs.
        r"\bgreat\s+(post|thread|take)!?\s*" + _EMOJI_CLASS + r"[^.\n]{0,200}\b(check|stop\s+by|visit|see)\b",
        "selfishly self-promoting: 'great post! check out my…'",
    ),
    (
        r"\b(check\s+out|stop\s+by|visit|see)\s+(my|our)\s+(stuff|site|product|profile|page)\b",
        "selfishly self-promoting: explicit self-link CTA in a reply",
    ),
    (
        r"\b(amazing|incredible|love\s+this|fire|absolute\s+banger)!?\s*" + _EMOJI_CLASS + r"+\s*$",
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
    # ---- Phase 10 — eight new categories from Daniel's voice anchor ----
    # engagement_bait: curiosity gap framings that promise a payoff the
    # reply doesn't deliver. Same surface as §28.2 #12 dark-pattern
    # engagement bait, but firing at draft-save time on REPLY text.
    (
        r"\b\d+\s+secrets?\b[^.\n]{0,200}\b(don't|do not|nobody|no one)\s+(know|tell|tells)\b",
        "engagement_bait: 'N secrets X don't know' framing",
    ),
    (
        r"\bnumber\s+\d+\s+will\s+(surprise|shock|amaze)",
        "engagement_bait: 'number N will surprise you' framing",
    ),
    (
        r"\byou\s+won't\s+believe\b",
        "engagement_bait: 'you won't believe' framing",
    ),
    # ragebait: manufactured opposition in REPLY text. Distinct from
    # reply_targets.lint_category='ragebait' which scores the TARGET post.
    (
        r"\bunpopular\s+opinion\b",
        "ragebait: 'unpopular opinion' framing",
    ),
    (
        r"\b(change|fight|prove)\s+my\s+mind\b",
        "ragebait: 'change my mind / fight me' framing",
    ),
    (
        r"\b(everyone|everybody|nobody|no\s+one)\s+(in\s+this\s+thread|here|is)\s+(is\s+)?wrong\b",
        "ragebait: 'everyone is wrong' tribal framing",
    ),
    # manipulative_question: questions wearing rhetorical clothing —
    # the writer has a fixed take but lowers the reader's guard with
    # false uncertainty.
    (
        r"\banyone\s+else\s+(think|feel|notice|see)\b[^.\n]{0,40}\?",
        "manipulative_question: 'anyone else…?' false-uncertainty bait",
    ),
    (
        r"\bam\s+i\s+(crazy|the\s+only|alone|missing\s+something)\b[^.\n]{0,30}\?",
        "manipulative_question: 'am I crazy?' false-uncertainty bait",
    ),
    # fake_authority: creator-economy credential inflation. The §28.2
    # #12 dark-pattern lint catches the most egregious cases; this
    # surface catches the subtler "after scaling X to Y" pattern.
    (
        r"\bafter\s+(scaling|building|growing|launching)\s+(\d+\+?|over\s+\d+|hundreds?\s+of)\b",
        "fake_authority: inflated scale claim",
    ),
    (
        r"\bas\s+someone\s+who'?s?\s+(worked\s+with|coached|advised|helped|scaled)\s+(\d+\+?|hundreds?\s+of|thousands?\s+of)\b",
        "fake_authority: 'as someone who's worked with N…' inflation",
    ),
    # performative_threading: 🧵 1/ on non-sequential content. Single
    # post pretending to be a thread.
    (
        r"\U0001F9F5\s*1\s*/",
        "performative_threading: '🧵 1/' on non-thread content",
    ),
    (
        r"(?:^|\s)1/\s+",
        "performative_threading: bare '1/ ' opener without a thread payload",
    ),
    # diving_preamble: throat-clearing before the first concrete
    # sentence. The reply starts when the post says something.
    (
        r"^\s*(let\s+me\s+(unpack|break|dive)|diving\s+(into|in)|breaking\s+this\s+down|hot\s+take\s+incoming|let'?s\s+(dive|unpack))\b",
        "diving_preamble: throat-clearing opener instead of a concrete first sentence",
    ),
    # emoji_as_personality: 2+ consecutive decorative emoji (not a
    # thumbs-up on a real artifact). The §29.10 _EMOJI_CLASS captures
    # the codepoints; this rule fires on chains of them.
    (
        r"(?:" + _EMOJI_CLASS + r"\s*){2,}",
        "emoji_as_personality: decorative emoji chain (2+ in a row) used for tone",
    ),
    # hedging_that_erases: confidence-eroding strings. Two or more
    # hedges in close proximity is the tell (a single "maybe" alone
    # is honest; "kind of, sort of, maybe, no expert but…" is the
    # eraser pattern).
    (
        r"\b(kind\s+of|sort\s+of)\b[^.\n]{0,80}\b(maybe|perhaps|just\s+thinking|no\s+expert)\b",
        "hedging_that_erases: stacked hedges that subtract the substance",
    ),
    (
        r"\b(just\s+thinking\s+out\s+loud|no\s+expert\s+but|not\s+sure\s+if\s+this\s+makes\s+sense)\b",
        "hedging_that_erases: 'just thinking out loud / no expert but' eraser",
    ),
)

# Phase 10 — the canonical eleven-value failure-mode enum. Mirrors the
# CHECK constraint on agent_drafts.reply_quality_lint_failure_mode in
# migration 023; the lint module is the single source of truth in code
# and the migration is the single source of truth in schema. Tests pin
# the equality.
REPLY_QUALITY_FAILURE_MODES: tuple[str, ...] = (
    "forced",
    "ai_tasting",
    "selfishly_self_promoting",
    "engagement_bait",
    "ragebait",
    "manipulative_question",
    "fake_authority",
    "performative_threading",
    "diving_preamble",
    "emoji_as_personality",
    "hedging_that_erases",
)


def _label_to_failure_mode(label: str) -> str:
    """Map a pattern label string to the canonical enum value.

    The label strings are human-readable for log surfaces; the enum
    values are the persistent-schema contract. Centralizing the
    mapping here keeps the pattern table cosmetic-free while staying
    one-to-one with the eleven enum values.
    """
    label_lower = label.lower()
    if "selfishly self-promoting" in label_lower or "selfishly_self_promoting" in label_lower:
        return "selfishly_self_promoting"
    if "ai-tasting" in label_lower or "ai_tasting" in label_lower:
        return "ai_tasting"
    if "engagement_bait" in label_lower or "engagement-bait" in label_lower:
        return "engagement_bait"
    if "ragebait" in label_lower:
        return "ragebait"
    if "manipulative_question" in label_lower:
        return "manipulative_question"
    if "fake_authority" in label_lower:
        return "fake_authority"
    if "performative_threading" in label_lower:
        return "performative_threading"
    if "diving_preamble" in label_lower:
        return "diving_preamble"
    if "emoji_as_personality" in label_lower:
        return "emoji_as_personality"
    if "hedging_that_erases" in label_lower:
        return "hedging_that_erases"
    # Defensive default — covers the legacy "forced: ..." labels and
    # any future label string that doesn't carry an explicit enum
    # token. "forced" was the legacy catch-all in Phase 5.9, so
    # preserving it here is the right backstop.
    return "forced"


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
    """Map the eleven-option Haiku response to a ReplyQualityResult.

    Phase 10: expanded from the original four-option set to recognize all
    eleven failure modes. The matcher tolerates leading whitespace, code
    fences, and the model occasionally using the human-readable category
    label instead of the enum token. Each branch returns the canonical
    enum value via _label_to_failure_mode (or direct token match).

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
    # Iterate the eleven enum values in order of specificity so a
    # response that mentions multiple categories (rare but possible)
    # resolves to the most-specific match. The order here matches
    # REPLY_QUALITY_FAILURE_MODES with the legacy "forced" last so
    # the broad legacy term doesn't shadow a more-specific Phase 10
    # category. ai-tasting handled separately because the legacy
    # response text uses "AI-tasting" (with hyphen).
    if "ai-tasting" in text or "ai tasting" in text or "ai_tasting" in text:
        return ReplyQualityResult(
            passed=False, rationale=body.strip(), failure_mode="ai_tasting"
        )
    if (
        "self-promoting" in text
        or "selfishly" in text
        or "selfishly_self_promoting" in text
    ):
        return ReplyQualityResult(
            passed=False,
            rationale=body.strip(),
            failure_mode="selfishly_self_promoting",
        )
    # Phase 10 — eight new categories. Each scanned by its enum token
    # (canonical) AND its space-separated variant (which Haiku sometimes
    # emits when it's quoting the human-readable name).
    for mode_token, alt_phrases in (
        ("engagement_bait", ("engagement bait", "engagement-bait")),
        # NOTE: ragebait must scan BEFORE other tokens that contain "bait"
        # for prefix-matching robustness.
        ("ragebait", ("ragebait", "rage bait", "rage-bait")),
        ("manipulative_question", ("manipulative question", "manipulative-question")),
        ("fake_authority", ("fake authority", "fake-authority")),
        ("performative_threading", ("performative threading", "performative-threading")),
        ("diving_preamble", ("diving preamble", "diving-preamble")),
        ("emoji_as_personality", ("emoji as personality", "emoji-as-personality")),
        ("hedging_that_erases", ("hedging that erases", "hedging-that-erases")),
    ):
        if mode_token in text or any(p in text for p in alt_phrases):
            return ReplyQualityResult(
                passed=False,
                rationale=body.strip(),
                failure_mode=mode_token,
            )
    if "forced" in text and text.startswith("yes"):
        return ReplyQualityResult(
            passed=False, rationale=body.strip(), failure_mode="forced"
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
    """Deterministic pattern matcher — used in tests + as API fallback.

    Phase 10: covers the eleven failure-mode categories. The
    ``_label_to_failure_mode`` helper centralises the label-to-enum
    mapping so the regex table stays purely cosmetic. The eleven
    categories are listed in ``REPLY_QUALITY_FAILURE_MODES``.
    """
    for pat, label in _REPLY_QUALITY_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            mode = _label_to_failure_mode(label)
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


# ===========================================================================
# Phase 7 / §29.10 — thread-classifier lint.
# ===========================================================================
# Distinct from the §28.18 reply_quality_lint shipped in Phase 5.9 (above):
#
# - reply_quality_lint scans Daniel's DRAFT REPLY ('does this reply read as
#   forced / AI-tasting / selfishly self-promoting?').
# - thread_classifier_lint scans the TARGET POST's THREAD QUALITY before
#   Daniel even starts drafting ('is this thread worth replying under?').
#
# Both lints can run on the same candidate. They write to different
# columns (§28.18 → agent_drafts.reply_quality_lint_passed; §29.10 →
# reply_targets.{lint_thread_classification_json, lint_category, lint_blocked}).
#
# Output schema per §29.10:
#   { ragebait: bool,
#     meme_with_no_serious_reply_path: bool,
#     low_quality_reply_thread: bool,
#     hijacking_required_to_mention_stir: bool,
#     rationale: str }
#
# Blocking rules (enforced by the caller in tools.py::score_reply_candidates):
#   - ragebait OR hijacking_required_to_mention_stir → lint_blocked=True;
#     lint_category set to the primary block; row dims in Queue UI;
#     'Draft reply' button disabled. Daniel can override via the
#     Force-draft affordance per §29.7 (mandatory reason).
#   - meme_with_no_serious_reply_path AND low_quality_reply_thread are
#     signals only — each subtracts 1 from reply_opportunity_score
#     (floored at 0). Never block on their own.
#
# Gated by ``reply_target_lint_enabled`` setting (default true; same
# discipline as reply_quality_lint).


_THREAD_LINT_CATEGORIES: tuple[str, ...] = (
    "ragebait",
    "meme_with_no_serious_reply_path",
    "low_quality_reply_thread",
    "hijacking_required_to_mention_stir",
)


# Offline pattern matchers — used in tests + as Haiku-outage fallback.
# Conservative: false positives let Daniel override via Force-draft;
# false negatives surface as Daniel encountering ragebait in the Queue
# and using the Skip dropdown.
#
# The "hijacking_required" pattern looks for self-promotional language
# patterns that would only register on Daniel's draft side; on the
# THREAD side, it's the target post's topic being SO far from Daniel's
# pillars that mentioning Stir would require hijacking. Hard to detect
# offline without semantic understanding — the offline matcher is
# intentionally narrow (just catches obvious "kitchen-frustration"
# off-topic threads when Daniel's niche is software). Production
# behavior leans on the Haiku call.
_RAGEBAIT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bunpopular\s+opinion\b", "ragebait: 'unpopular opinion' framing"),
    # RV2-26: bounded character class — thread classifier inputs can be longer.
    (r"\b(everyone|nobody)\s+(is|will|wants?)\b[^.\n]{0,200}[?!]", "ragebait: us-vs-them framing"),
    (r"\bchange\s+my\s+mind\b", "ragebait: 'change my mind' framing"),
    (r"\b(prove|fight)\s+me\s+wrong\b", "ragebait: 'fight me / prove me wrong' framing"),
    (r"\b(woke|cancel\s+culture|libtard|trumptard)\b", "ragebait: tribal-culture-war terms"),
)
_MEME_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^\s*[A-Z\s]{5,}!?\s*$", "meme: shouting / all-caps single-line"),
    (r"^\s*\S+\s*[?!]+\s*$", "meme: bare-word reaction post"),
)
_LOW_QUALITY_THREAD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(rt|retweet)\s+(if|for|to)\b", "low-quality: RT-bait engagement-farming"),
    (r"\b(like\s+if|follow\s+if|reply\s+below\s+with)\b", "low-quality: explicit engagement bait"),
)


@dataclass(frozen=True)
class ThreadLintResult:
    """Output of the §29.10 thread-classifier lint pass.

    All four boolean flags carry independent signals; the caller maps them
    onto ``lint_blocked`` + ``lint_category`` + the
    ``reply_opportunity_score`` signal subtraction.

    ``rationale`` is the one-line human-readable explanation surfaced as
    a tooltip in the Queue UI alongside the dimmed row.

    ``model_used`` carries the model id on a live call, ``'offline'`` on
    a deterministic pattern match, ``'disabled'`` when the lint was
    short-circuited via ``reply_target_lint_enabled=false``,
    ``'offline-fallback'`` on Haiku unreachability.
    """

    ragebait: bool
    meme_with_no_serious_reply_path: bool
    low_quality_reply_thread: bool
    hijacking_required_to_mention_stir: bool
    rationale: str
    model_used: str | None = None
    api_call_failed: bool = False

    @property
    def is_blocking(self) -> bool:
        """True iff §29.10 says the candidate's 'Draft reply' button is disabled."""
        return self.ragebait or self.hijacking_required_to_mention_stir

    @property
    def primary_category(self) -> str | None:
        """Denormalized primary category for the reply_targets.lint_category column.

        Returns the first applicable category per the spec's display
        precedence (ragebait outranks hijacking which outranks the two
        signal-only ones). NULL when none fired.
        """
        if self.ragebait:
            return "ragebait"
        if self.hijacking_required_to_mention_stir:
            return "hijacking_required_to_mention_stir"
        if self.meme_with_no_serious_reply_path:
            return "meme_with_no_serious_reply_path"
        if self.low_quality_reply_thread:
            return "low_quality_reply_thread"
        return None

    def to_json(self) -> str:
        """Serialize for the lint_thread_classification_json column."""
        return json.dumps(
            {
                "ragebait": self.ragebait,
                "meme_with_no_serious_reply_path": self.meme_with_no_serious_reply_path,
                "low_quality_reply_thread": self.low_quality_reply_thread,
                "hijacking_required_to_mention_stir": (
                    self.hijacking_required_to_mention_stir
                ),
                "rationale": self.rationale,
            }
        )


_THREAD_CLASSIFIER_PROMPT = """You are reviewing an X post that someone is considering replying to.
Classify the TARGET POST's thread quality on four orthogonal flags:

1. ragebait — the post is designed to provoke arguments via tribal /
   us-vs-them framing, 'unpopular opinion' bait, 'change my mind',
   culture-war terms, or other patterns that engineer outrage over
   substance.

2. meme_with_no_serious_reply_path — the post is a meme / joke / one-
   word reaction with no factual or experiential angle a substantive
   reply could add. Replies under this kind of post are necessarily
   forced.

3. low_quality_reply_thread — the post's existing replies are
   engagement-farming ('RT if you agree', 'reply with X'), low-effort
   chat-room noise, or otherwise occupied by an audience that won't
   reward a substantive reply.

4. hijacking_required_to_mention_stir — Daniel's product is Stir
   (parent-friendly meal planning). This flag should fire ONLY when
   the target post's topic is SO far from cooking / parenting / meal-
   planning that bringing up Stir would feel like hijacking. (Ignore
   this flag if Daniel's niche definition is different from cooking;
   the niche context is provided via the niche_problem field below.)

Return STRICTLY JSON with exactly these keys:
  ragebait: true | false
  meme_with_no_serious_reply_path: true | false
  low_quality_reply_thread: true | false
  hijacking_required_to_mention_stir: true | false
  rationale: one-line explanation (max ~120 chars)

Do NOT include any text outside the JSON object.

Target post author: @{author}
Target post text:
{post_text}

Observed engagement metrics: {metrics}

Daniel's niche (for the 'hijacking_required' decision):
{niche_problem}
"""


def _offline_thread_classifier(
    target_post_text: str,
    target_author_handle: str,
) -> ThreadLintResult:  # noqa: ARG001 — author kept for symmetry + future heuristics
    """Deterministic pattern matcher — tests + Haiku-outage fallback."""
    text = target_post_text or ""

    ragebait_hits: list[str] = []
    for pat, label in _RAGEBAIT_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            ragebait_hits.append(label)

    meme_hits: list[str] = []
    for pat, label in _MEME_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            meme_hits.append(label)

    low_q_hits: list[str] = []
    for pat, label in _LOW_QUALITY_THREAD_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            low_q_hits.append(label)

    # Hijacking is semantic — offline matcher can't determine niche
    # alignment without the niche context. Default to False; production
    # leans on the Haiku call.
    hijacking = False

    pieces: list[str] = []
    if ragebait_hits:
        pieces.append("ragebait(" + "; ".join(ragebait_hits) + ")")
    if meme_hits:
        pieces.append("meme(" + "; ".join(meme_hits) + ")")
    if low_q_hits:
        pieces.append("low_quality(" + "; ".join(low_q_hits) + ")")

    rationale = (
        "offline thread lint: " + ", ".join(pieces) if pieces
        else "offline thread lint: no failure-mode patterns matched"
    )
    return ThreadLintResult(
        ragebait=bool(ragebait_hits),
        meme_with_no_serious_reply_path=bool(meme_hits),
        low_quality_reply_thread=bool(low_q_hits),
        hijacking_required_to_mention_stir=hijacking,
        rationale=rationale,
        model_used="offline",
    )


def is_thread_classifier_lint_enabled(value_json: str | None) -> bool:
    """Parse the ``reply_target_lint_enabled`` setting value_json."""
    if value_json is None:
        return True
    try:
        return bool(json.loads(value_json))
    except (json.JSONDecodeError, ValueError):
        return True


def _parse_thread_classifier_response(body: str) -> ThreadLintResult | None:
    """Map a Haiku response body to a ThreadLintResult. None on parse failure
    so the caller can route through the offline matcher."""
    text = (body or "").strip()
    if not text:
        return None
    # Tolerate a leading code-fence (occasionally emitted by the model).
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return ThreadLintResult(
        ragebait=bool(data.get("ragebait", False)),
        meme_with_no_serious_reply_path=bool(
            data.get("meme_with_no_serious_reply_path", False)
        ),
        low_quality_reply_thread=bool(data.get("low_quality_reply_thread", False)),
        hijacking_required_to_mention_stir=bool(
            data.get("hijacking_required_to_mention_stir", False)
        ),
        rationale=str(data.get("rationale", "") or "(no rationale provided)"),
    )


def thread_classifier_lint(
    target_post_text: str,
    target_author_handle: str,
    observed_metrics: dict | None = None,
    niche_problem: str | None = None,
    *,
    model: str = "claude-haiku-4-5",
    enabled: bool = True,
) -> ThreadLintResult:
    """§29.10 thread-classifier lint over a candidate target post.

    Distinct surface from ``reply_quality_lint`` (§28.18) — see the
    module-level disambiguation comment above.

    When ``enabled=False`` (the setting ``reply_target_lint_enabled`` is
    off), short-circuits to all-False with rationale='lint disabled' so
    the audit row records that the lint did not run.

    Honors ``LINT_OFFLINE=1`` env var: skips the Haiku call and runs the
    deterministic pattern matcher only (same env-var convention as
    dark-pattern + reply-quality lints).

    When Haiku is unreachable, falls back to the offline matcher with
    ``model_used='offline-fallback'`` and ``api_call_failed=True``.

    Returns a ``ThreadLintResult`` — never raises. The caller in
    ``tools.py::score_reply_candidates`` interprets ``is_blocking``
    and ``primary_category`` to set the reply_targets columns.
    """
    if not enabled:
        return ThreadLintResult(
            ragebait=False,
            meme_with_no_serious_reply_path=False,
            low_quality_reply_thread=False,
            hijacking_required_to_mention_stir=False,
            rationale="lint disabled",
            model_used="disabled",
        )

    if os.environ.get("LINT_OFFLINE") == "1":
        return _offline_thread_classifier(target_post_text, target_author_handle)

    try:
        import anthropic
    except ImportError:
        return _offline_thread_classifier(target_post_text, target_author_handle)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _offline_thread_classifier(target_post_text, target_author_handle)

    metrics_str = json.dumps(observed_metrics or {}, sort_keys=True)
    niche_str = (niche_problem or "(niche not provided)").strip()
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[
                {
                    "role": "user",
                    "content": _THREAD_CLASSIFIER_PROMPT.format(
                        author=target_author_handle or "(handle not provided)",
                        post_text=target_post_text or "(post text not provided)",
                        metrics=metrics_str,
                        niche_problem=niche_str,
                    ),
                }
            ],
        )
        body = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                body = block.text
                break
        parsed = _parse_thread_classifier_response(body)
        if parsed is None:
            fallback = _offline_thread_classifier(
                target_post_text, target_author_handle
            )
            return ThreadLintResult(
                ragebait=fallback.ragebait,
                meme_with_no_serious_reply_path=fallback.meme_with_no_serious_reply_path,
                low_quality_reply_thread=fallback.low_quality_reply_thread,
                hijacking_required_to_mention_stir=(
                    fallback.hijacking_required_to_mention_stir
                ),
                rationale=(
                    f"unparseable haiku response → offline fallback: "
                    f"{fallback.rationale}. raw: {body[:200]!r}"
                ),
                model_used="offline-fallback",
                api_call_failed=False,
            )
        return ThreadLintResult(
            ragebait=parsed.ragebait,
            meme_with_no_serious_reply_path=parsed.meme_with_no_serious_reply_path,
            low_quality_reply_thread=parsed.low_quality_reply_thread,
            hijacking_required_to_mention_stir=parsed.hijacking_required_to_mention_stir,
            rationale=parsed.rationale,
            model_used=model,
        )
    except Exception as exc:
        # Same outage discipline as the other two lints — fall back to
        # the offline matcher with api_call_failed=True so audit
        # reviewers can grep for intermittent outages without inferring
        # from rationale prose.
        fallback = _offline_thread_classifier(target_post_text, target_author_handle)
        return ThreadLintResult(
            ragebait=fallback.ragebait,
            meme_with_no_serious_reply_path=fallback.meme_with_no_serious_reply_path,
            low_quality_reply_thread=fallback.low_quality_reply_thread,
            hijacking_required_to_mention_stir=(
                fallback.hijacking_required_to_mention_stir
            ),
            rationale=(
                f"offline-fallback (haiku unreachable: {type(exc).__name__}). "
                f"offline result: {fallback.rationale}"
            ),
            model_used="offline-fallback",
            api_call_failed=True,
        )


# ---------------------------------------------------------------------------
# RV2-27: public re-exports of the offline pattern catalogues.
# ---------------------------------------------------------------------------
# Tests + ops tooling can address these directly without touching private
# names. If a future false positive needs a targeted regression test, the
# entry → label mapping is here. Same tuple objects as the private
# definitions above — no duplication; just a public alias.
ENGAGEMENT_BAIT_PATTERNS = _ENGAGEMENT_BAIT_PATTERNS
REPLY_QUALITY_PATTERNS = _REPLY_QUALITY_PATTERNS
RAGEBAIT_PATTERNS = _RAGEBAIT_PATTERNS
MEME_PATTERNS = _MEME_PATTERNS
LOW_QUALITY_THREAD_PATTERNS = _LOW_QUALITY_THREAD_PATTERNS
