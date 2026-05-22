"""Pre-publish heuristic scorer (§28.11) — a deterministic read of a draft
just before save, surfaced as a colored chip in Today / Next Rep / Agent
Chat.

Design rules (read first):

1. **Deterministic-first.** Each score function is a pure Python function
   of the draft text + lightweight metadata. Same inputs → same outputs.
   No LLM call by default. The §28.11 setting
   `prepublish_scorer_llm_augmentation_enabled` (default false) layers an
   optional warnings_json second pass — it does NOT modify the
   deterministic scores.
2. **Never blocks publish.** The orchestrator persists the score row and
   wires `agent_drafts.prepublish_score_id`; the §28.10 click-handler
   never consults `prepublish_scores`. Hard gates live in IWH +
   dark-pattern lint.
3. **`composite_label` is the only thing the UI shows by default.** No
   numeric composite — the precision the underlying 0-3 scores would
   suggest is more precision than the input supports. Mirrors §11
   v_lane_performance's graduated-confidence discipline.

The dimension definitions in the spec are the contract; the docstrings on
each `*_score` function below are the operational gloss. Tune the
constants here; do not move the contract.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from app.agent import voice_profile as _voice_profile

SCORER_VERSION = "prepublish-scorer/0.1.0"

# Length anchors (mirror settings keys x_short_post_target_chars and
# x_post_max_chars, hardcoded here because the scorer should not
# spontaneously change behavior on a settings-row edit during a session;
# operator changes the constants here AND bumps SCORER_VERSION).
SHORT_POST_TARGET_CHARS = 200
POST_MAX_CHARS = 280

# Composite-label derivation thresholds (§10 prepublish_scores notes).
STRONG_MIN_TWOS = 6
STRONG_MIN_THREES = 2
WEAK_MAX_TWOS = 3

# Cheap "feels-like-an-LLM" markers — when several appear in one draft,
# voice_fit_score takes a meaningful hit. Each was picked from voice
# samples that explicitly call them out as off-voice; tune by editing the
# list and bumping SCORER_VERSION.
_LLM_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bunlock(ing)?\b your potential\b",
        r"\bleverage\b",
        r"\bnavigate\b (?:the|this|your)",
        r"\bdive deep(?:er)?\b",
        r"\bgame[- ]changer\b",
        r"\bsynergy\b",
        r"\bdelve into\b",
        r"\bworld of\b",
        r"\bin today'?s (?:fast[- ]paced|digital|modern) world\b",
        r"\bat the end of the day\b",
        r"\bwear(?:ing|s)? many hats\b",
    )
)

# Generic openers — cheap "first line is generic" detector.
_GENERIC_OPENER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^just\b",
        r"^so\b",
        r"^honestly\b",
        r"^you know\b",
        r"^thoughts\?",
        r"^i'?ve been thinking\b",
        r"^let'?s talk\b",
        r"^here'?s the thing\b",
    )
)


@dataclass(frozen=True)
class ScoreRow:
    clarity_score: int
    hook_strength_score: int
    specificity_score: int
    length_fit_score: int
    format_fit_score: int
    topic_fit_score: int
    reply_substance_score: int | None
    cta_strength_score: int | None
    voice_fit_score: int | None
    composite_label: str
    warnings_json: list[str]
    scorer_version: str = SCORER_VERSION

    def as_db_tuple(self, draft_id: int) -> tuple:
        """Return the positional tuple matching the INSERT column order in
        `_insert_score_row`."""
        return (
            draft_id,
            self.clarity_score,
            self.hook_strength_score,
            self.specificity_score,
            self.length_fit_score,
            self.format_fit_score,
            self.topic_fit_score,
            self.reply_substance_score,
            self.cta_strength_score,
            self.voice_fit_score,
            self.composite_label,
            None if not self.warnings_json else _json_dump(self.warnings_json),
            self.scorer_version,
        )


def _json_dump(items: list[str]) -> str:
    import json
    return json.dumps(items, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Sentence / line helpers.
# ---------------------------------------------------------------------------
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_BREAK.split(text.strip()) if s.strip()]
    return parts


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


# ---------------------------------------------------------------------------
# Per-dimension scorers. Each docstring spells out what 0/1/2/3 mean.
# ---------------------------------------------------------------------------
def clarity_score(text: str) -> int:
    """0-3. One idea, clean syntax, parses at 0.8s of attention.

    - 3: short to medium sentences, no nested clauses, single idea per line.
    - 2: mostly clean, one slightly compound sentence or one heavy clause.
    - 1: parsing required, multiple ideas crammed in.
    - 0: jargon-dense or sentence-runaway (>30 words in one sentence).
    """
    sentences = _split_sentences(text)
    if not sentences:
        return 0
    max_words = max(len(s.split()) for s in sentences)
    runaway = max_words > 30
    very_long = max_words > 22
    long_count = sum(1 for s in sentences if len(s.split()) > 18)
    if runaway:
        return 0
    if very_long and long_count >= 2:
        return 1
    if long_count >= 1:
        return 2
    return 3


def hook_strength_score(text: str) -> int:
    """0-3. Does the first line stop a scroll?

    - 3: concrete noun + specific carrier; would survive cold scroll.
    - 2: specific but not striking.
    - 1: passable opener, could be any post.
    - 0: matches a generic-opener pattern OR starts with a verb-of-being.
    """
    first_line = _lines(text)[0] if _lines(text) else ""
    if not first_line:
        return 0
    for pat in _GENERIC_OPENER_PATTERNS:
        if pat.search(first_line):
            return 0
    # P58R-17 — strip URLs and hashtags before the digit / proper-noun
    # check so incidental digits inside an URL (`/status/1234`) or a
    # hashtag (`#build2024`) don't fake-pass the "concrete signal" gate.
    cleaned_first_line = re.sub(r"https?://\S+|#\w+", " ", first_line)
    # Concrete signal: a digit OR a proper-noun-shaped word, plus length.
    # Any digit anywhere counts — "7pm" is concrete even though `\b\d+\b`
    # would miss it because `pm` is a word char with no boundary.
    has_digit = bool(re.search(r"\d", cleaned_first_line))
    has_proper = bool(re.search(r"\b[A-Z][a-zA-Z]{2,}\b", cleaned_first_line))
    words = first_line.split()
    if has_digit and len(words) >= 6:
        return 3
    if (has_digit or has_proper) and len(words) >= 5:
        return 2
    if len(words) >= 4:
        return 1
    return 0


def specificity_score(text: str) -> int:
    """0-3. Real nouns / numbers / artifacts vs. abstract filler.

    Heuristic: counts of (numbers) + (proper-noun-shaped tokens) +
    (concrete-noun tokens not in the vague-noun blacklist).
    """
    digits = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    proper = len(re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text))
    vague = len(
        re.findall(
            r"\b(?:things?|people|many|some|stuff|various|several|lots?)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    signal = digits * 2 + proper
    if vague >= 3:
        return 0
    if signal >= 5 and vague <= 1:
        return 3
    if signal >= 3:
        return 2
    if signal >= 1:
        return 1
    return 0


def length_fit_score(text: str, draft_kind: str = "standalone") -> int:
    """0-3. Within target chars; long form earns its length.

    Hard ceiling at POST_MAX_CHARS (280) → 0 across all draft kinds.

    Replies legitimately run much shorter than a standalone (a tight
    `"yes — 3x in 24h."` reply is the spec's positive anchor, not the
    negative one). They earn 3 anywhere from 10 to 240 chars, 2 from
    240 to the 280 ceiling, and 1 below 10 chars (still readable but
    likely too thin).

    Standalones use the §28.11 target band: within ~10% of
    SHORT_POST_TARGET_CHARS (200) earns 3, within ~25% earns 2,
    longer-but-under-ceiling earns 1, far-under-target (n<20) earns 0.
    """
    n = len(text)
    if n == 0:
        return 0
    if n > POST_MAX_CHARS:
        return 0
    if draft_kind == "reply":
        if n < 10:
            return 1
        if n <= 240:
            return 3
        return 2
    if n < 20:
        return 0
    target = SHORT_POST_TARGET_CHARS
    delta_pct = abs(n - target) / target
    if delta_pct <= 0.10:
        return 3
    if delta_pct <= 0.25:
        return 2
    return 1


def format_fit_score(text: str) -> int:
    """0-3. Sentence-per-line rhythm; ending lands.

    - 3: multi-line with deliberate breaks; final line is a clear stop.
    - 2: single-line but ending is decisive.
    - 1: wall of text but parseable.
    - 0: trails off (ends in "..." or with a hanging connective).
    """
    stripped = text.rstrip()
    if stripped.endswith("...") or stripped.endswith(" and") or stripped.endswith(","):
        return 0
    lines = _lines(text)
    sentence_count = len(_split_sentences(text))
    if len(lines) >= 3 and sentence_count >= 2:
        return 3
    if sentence_count >= 2 and len(text) < 240:
        return 2
    return 1


def topic_fit_score(text: str, pillar: str | None) -> int:
    """0-3. Sits inside the declared pillar.

    Without a pillar tag (pillar None) the dimension defaults to 2 —
    declared-pillar drift is the only way to fall below it. Each pillar
    carries a small affinity vocabulary, hand-curated for MVP.
    """
    if pillar is None or pillar == "":
        return 2
    pillar = str(pillar).lower()
    affinity = {
        "stir": (
            "stir", "dinner", "kitchen", "cook", "recipe", "ingredient",
            "fridge", "parent", "scan", "meal", "pantry",
        ),
        "build": (
            "ship", "build", "code", "agent", "tool", "iterate", "prototype",
            "stack", "deploy", "test", "user", "feedback",
        ),
        "self": (
            "learn", "habit", "practice", "morning", "routine", "reflect",
            "discipline", "consistency", "rep", "fail",
        ),
    }
    words = re.findall(r"[A-Za-z]+", text.lower())
    if not words:
        return 0
    pillar_words = affinity.get(pillar, ())
    hits = sum(1 for w in words if w in pillar_words)
    if hits >= 3:
        return 3
    if hits >= 1:
        return 2
    return 1


def reply_substance_score(text: str, target_post_text: str | None) -> int:
    """0-3. Address the original post before pivoting to Daniel's angle.

    - 3: leading clause addresses target substantively.
    - 2: addresses target but pivot dominates.
    - 1: pivot only, weak target tie-in.
    - 0: "great post" / "this" / "so true" — thin acknowledgment.
    """
    lower = text.lower().strip()
    thin_openers = ("great post", "this", "so true", "agreed", "love this", "+1", "facts")
    if not target_post_text:
        # No target text known. The thin-opener check still applies — a
        # "great post!" reply is thin regardless of whether we can read
        # the target. Reserve the middle "2" for the case below where a
        # real target exists but lexical overlap is sparse.
        for opener in thin_openers:
            if lower.startswith(opener) and len(text) < 80:
                return 0
        return 1
    for opener in thin_openers:
        if lower.startswith(opener) and len(text) < 80:
            return 0
    # Lexical overlap proxy using cheap prefix-5 stemming so "rewards" and
    # "rewarding" or "specific" and "specifically" collide, but "specific"
    # and "speculation" do not. Just enough fuzziness to avoid scoring a
    # plainly-substantive reply low because the verb tense shifted.
    def _stems(s: str) -> set[str]:
        return {t.lower()[:5] for t in re.findall(r"[A-Za-z]{4,}", s)}
    tokens_target = _stems(target_post_text)
    tokens_reply_lead = _stems(text.split("\n", 1)[0])
    overlap = len(tokens_target & tokens_reply_lead)
    if overlap >= 3:
        return 3
    if overlap >= 1:
        return 2
    return 1


def cta_strength_score(text: str, cta: str | None) -> int | None:
    """0-3. Clear ask matching the cta field. None when cta='none'.

    cta='none' returns None so the composite-label derivation skips it
    rather than treating absence as a 0. The spec is explicit about this:
    "cta_strength_score is NULL when cta = none."
    """
    if cta is None or str(cta).lower() == "none":
        return None
    last_line = _lines(text)[-1] if _lines(text) else ""
    # Question mark, imperative verb, or "let me know" / "DM me" pattern.
    if not last_line:
        return 0
    # P58R-16 — a one- or two-word generic question ("thoughts?", "agreed?",
    # "views?") is the textbook generic CTA. Treat as 0 rather than 1.
    if re.search(
        r"^(thoughts?|agreed|views?|opinions?|takes?)\??\.?$",
        last_line.strip(),
        re.IGNORECASE,
    ):
        return 0
    if "?" in last_line and len(last_line.split()) >= 3:
        return 3
    if re.search(r"\b(reply|dm|comment|share|book|join|sign up|grab)\b", last_line, re.IGNORECASE):
        return 3
    # Generic "what do you think?" — counts but weak.
    if re.search(r"what (do you|are your)\b", last_line, re.IGNORECASE):
        return 2
    if "?" in last_line:
        return 2
    return 1


def voice_fit_score(text: str, profile: _voice_profile.VoiceProfile | None) -> int | None:
    """0-3. Agreement with the active voice_profile.

    Returns None when no active profile exists (spec: voice_fit_score is
    NULL and the composite skips it).

    Heuristic:
      + presence of vocabulary_signatures (up to +1)
      + cadence proximity (one_idea_per_line_rate match → +1)
      − presence of stop_phrases (−1 each, capped at the floor)
      − presence of generic-LLM-phrase patterns (−1 each)
    """
    if profile is None:
        return None
    score = 2  # start at "viable"
    vocab = [v.lower() for v in profile.vocabulary_signatures() if v]
    stops = [s.lower() for s in profile.stop_phrases() if s]
    lower_text = text.lower()
    vocab_hits = sum(1 for v in vocab if v and v in lower_text)
    stop_hits = sum(1 for s in stops if s and s in lower_text)
    llm_hits = sum(1 for pat in _LLM_PHRASE_PATTERNS if pat.search(text))

    if vocab_hits >= 1:
        score += 1
    score -= stop_hits
    score -= llm_hits

    # Cadence match: if the profile says one_idea_per_line_rate >= 0.5,
    # reward multi-line drafts.
    cadence = profile.cadence()
    rate = cadence.get("one_idea_per_line_rate") if isinstance(cadence, dict) else None
    if isinstance(rate, (int, float)) and rate >= 0.5:
        line_count = len(_lines(text))
        if line_count >= 3:
            score += 1
        elif line_count <= 1:
            score -= 1

    return max(0, min(3, score))


# ---------------------------------------------------------------------------
# Composite label derivation. Pure function — unit-tested explicitly.
# ---------------------------------------------------------------------------
def compute_composite_label(scores: dict[str, int | None]) -> str:
    """Derive `weak | viable | strong` per §10 prepublish_scores notes.

    Iterates only over scores that are not None — NULL dimensions
    (cta_strength_score when cta='none', voice_fit_score when no active
    profile, reply_substance_score on non-replies) are simply skipped.
    """
    vals = [v for v in scores.values() if v is not None]
    if not vals:
        return "weak"  # degenerate; treat as weak rather than 'unknown'
    zero_count = sum(1 for v in vals if v == 0)
    two_plus = sum(1 for v in vals if v >= 2)
    three_count = sum(1 for v in vals if v == 3)
    if zero_count == 0 and two_plus >= STRONG_MIN_TWOS and three_count >= STRONG_MIN_THREES:
        return "strong"
    if zero_count >= 1 or two_plus <= WEAK_MAX_TWOS:
        return "weak"
    return "viable"


# ---------------------------------------------------------------------------
# Orchestrator entrypoint.
# ---------------------------------------------------------------------------
def score(
    *,
    draft_text: str,
    draft_kind: str,
    pillar: str | None,
    cta: str | None,
    target_post_text: str | None,
    active_voice_profile: _voice_profile.VoiceProfile | None,
) -> ScoreRow:
    """Score a draft. Pure function over its inputs.

    The orchestrator passes the active voice profile (looked up once at
    the start of the save_draft_* call); we never read DB here so the
    scorer stays testable without a fixture.

    Note (P58R-11): a future `audience_fit_score` dimension is on the
    Phase 5.X roadmap. When it lands, add an `audience` kwarg here AND
    update the call sites in `_save_draft_post` (standalone — has
    audience) and `_save_draft_reply` (reply — pass None or compute
    from the target). Until then, omit the param entirely so neither
    handler has to choose between asymmetric calls.
    """
    s_clarity = clarity_score(draft_text)
    s_hook = hook_strength_score(draft_text)
    s_specificity = specificity_score(draft_text)
    s_length = length_fit_score(draft_text, draft_kind)
    s_format = format_fit_score(draft_text)
    s_topic = topic_fit_score(draft_text, pillar)
    s_reply: int | None
    if draft_kind == "reply":
        s_reply = reply_substance_score(draft_text, target_post_text)
    else:
        s_reply = None
    s_cta = cta_strength_score(draft_text, cta)
    s_voice = voice_fit_score(draft_text, active_voice_profile)

    scores: dict[str, int | None] = {
        "clarity": s_clarity,
        "hook_strength": s_hook,
        "specificity": s_specificity,
        "length_fit": s_length,
        "format_fit": s_format,
        "topic_fit": s_topic,
        "reply_substance": s_reply,
        "cta_strength": s_cta,
        "voice_fit": s_voice,
    }
    label = compute_composite_label(scores)

    warnings: list[str] = []
    if s_hook == 0:
        warnings.append("hook is generic — first line could be any post")
    if s_specificity == 0:
        warnings.append("vague nouns; add a real number or named artifact")
    if s_length == 0:
        warnings.append(
            f"length out of bounds ({len(draft_text)} chars) — either too short to land or over the {POST_MAX_CHARS}-char ceiling"
        )
    if s_format == 0:
        warnings.append("ends without a clear stop")
    if s_voice == 0 and active_voice_profile is not None:
        warnings.append("voice fit weak — phrases match the stop-phrase or LLM list")
    if s_reply == 0:
        warnings.append("reply leads with thin acknowledgment; address the target substantively first")

    return ScoreRow(
        clarity_score=s_clarity,
        hook_strength_score=s_hook,
        specificity_score=s_specificity,
        length_fit_score=s_length,
        format_fit_score=s_format,
        topic_fit_score=s_topic,
        reply_substance_score=s_reply,
        cta_strength_score=s_cta,
        voice_fit_score=s_voice,
        composite_label=label,
        warnings_json=warnings,
    )


# ---------------------------------------------------------------------------
# DB write — called by _save_draft_post / _save_draft_reply.
# ---------------------------------------------------------------------------
def insert_score_row(
    conn: sqlite3.Connection, *, agent_draft_id: int, row: ScoreRow
) -> int:
    """Persist a score row and wire it back to agent_drafts.prepublish_score_id.

    Returns the new prepublish_scores.id. The cyclical FK
    (agent_drafts ↔ prepublish_scores) is set up here in two writes
    inside the caller's transaction.
    """
    cur = conn.execute(
        """
        INSERT INTO prepublish_scores
          (agent_draft_id, clarity_score, hook_strength_score,
           specificity_score, length_fit_score, format_fit_score,
           topic_fit_score, reply_substance_score, cta_strength_score,
           voice_fit_score, composite_label, warnings_json, scorer_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row.as_db_tuple(agent_draft_id),
    )
    score_id = int(cur.lastrowid)
    conn.execute(
        "UPDATE agent_drafts SET prepublish_score_id = ? WHERE id = ?",
        (score_id, agent_draft_id),
    )
    return score_id


def get_score_for_draft(conn: sqlite3.Connection, *, agent_draft_id: int) -> dict | None:
    """Read a score row by draft id. Returns dict-of-columns or None."""
    row = conn.execute(
        """
        SELECT id, agent_draft_id, scored_at_utc, clarity_score,
               hook_strength_score, specificity_score, length_fit_score,
               format_fit_score, topic_fit_score, reply_substance_score,
               cta_strength_score, voice_fit_score, composite_label,
               warnings_json, scorer_version, tokens_used
        FROM prepublish_scores
        WHERE agent_draft_id = ?
        """,
        (int(agent_draft_id),),
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}
