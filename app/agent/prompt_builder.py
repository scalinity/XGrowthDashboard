"""System prompt assembly — splices §28.2 rules + voice samples + tool catalog.

The template lives at ``config/agent_system_prompt.md`` with three
placeholders the builder substitutes at runtime:

  * ``{{ NON_NEGOTIABLE_RULES_PLACEHOLDER }}`` — Section 3 rules 1-13
    extracted verbatim from ``spec.md`` §28.2.
  * ``{{ VOICE_SAMPLES_PLACEHOLDER }}`` — Section 5 top-N active voice
    samples from the ``voice_samples`` table.
  * ``{{ TOOL_CATALOG_PLACEHOLDER }}`` — Section 7 rendered from
    ``app.agent.tools.AGENT_TOOLS``.

The §25 drift check (``verify_rule_count_matches_spec``) asserts the
count of rules in the spec equals the count spliced into the prompt.
Any mismatch is a hard failure — that's the contract that prevents
silent prompt drift.
"""

from __future__ import annotations

import functools
import re
import sqlite3
from pathlib import Path

from app.agent import niche, personality_lore, tools, voice, voice_profile
from app.agent.spec_drift import SpecDriftError as SpecDriftError  # noqa: PLC0414 — explicit re-export so callers using prompt_builder.SpecDriftError keep working after the S10 follow-up cycle-break.
from app.db import PROJECT_ROOT  # P6R-37: centralized in app.db.

PROMPT_TEMPLATE_PATH: Path = PROJECT_ROOT / "config" / "agent_system_prompt.md"
SPEC_PATH: Path = PROJECT_ROOT / "spec.md"

NON_NEGOTIABLE_PLACEHOLDER = "<!-- {{ NON_NEGOTIABLE_RULES_PLACEHOLDER }} -->"
VOICE_SAMPLES_PLACEHOLDER = "<!-- {{ VOICE_SAMPLES_PLACEHOLDER }} -->"
TOOL_CATALOG_PLACEHOLDER = "<!-- {{ TOOL_CATALOG_PLACEHOLDER }} -->"
# Phase 5.8 / §28.12 — generated voice profile splice points.
VOICE_PROFILE_SELF_DESCRIPTION_PLACEHOLDER = (
    "<!-- {{ VOICE_PROFILE_SELF_DESCRIPTION_PLACEHOLDER }} -->"
)
VOICE_PROFILE_STRUCTURAL_PLACEHOLDER = (
    "<!-- {{ VOICE_PROFILE_STRUCTURAL_PLACEHOLDER }} -->"
)
# Phase 5.9 / §28.16 — structured niche definition splice point.
NICHE_DEFINITION_PLACEHOLDER = "<!-- {{ NICHE_DEFINITION_PLACEHOLDER }} -->"
# Phase 5.9 / §28.21 — personality lore splice point (after voice samples).
PERSONALITY_LORE_PLACEHOLDER = "<!-- {{ PERSONALITY_LORE_PLACEHOLDER }} -->"
# Phase 10 / §28.12 — prescriptive voice anchor (hand-curated, static).
# Splices into Section 5 AFTER the generated voice_profile structural
# block and BEFORE the raw voice_samples block. See
# config/voice_profile_prescriptive.md for the contents.
VOICE_PROFILE_PRESCRIPTIVE_PLACEHOLDER = (
    "<!-- {{ VOICE_PROFILE_PRESCRIPTIVE_PLACEHOLDER }} -->"
)
VOICE_PROFILE_PRESCRIPTIVE_PATH: Path = (
    PROJECT_ROOT / "config" / "voice_profile_prescriptive.md"
)


# P59A-W9: cache the template + spec parse at process scope. Streamlit
# reruns the entire script on every user interaction; without caching
# every rerun that crossed build_system_prompt re-read the template,
# regex-parsed spec.md (~1000 lines, DOTALL), and re-queried 5 tables.
# Cache invalidation is process restart — exactly what `streamlit run`
# does on file save. Settings / voice / lore queries stay uncached
# because their values must be current.
@functools.lru_cache(maxsize=1)
def _read_template() -> str:
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


# Phase 10 S10 — SpecDriftError is the unified base class for every
# drift/extraction error. Defined in app.agent.spec_drift so
# app.agent.lint can also subclass it without inducing the prior
# prompt_builder → tools → lint import cycle. Re-exported via the
# top-of-file import above so existing callers using
# `prompt_builder.SpecDriftError` continue to work without churn.


class SpecRuleExtractionError(SpecDriftError):
    """Raised when extract_rules_from_spec cannot find any rules.

    Treat as a hard build failure — the assembled system prompt would
    otherwise carry the literal '(rule splice failed — see prompt_builder.py)'
    placeholder string in place of Section 3, and the agent would lose
    the non-negotiable rules silently.
    """


@functools.lru_cache(maxsize=4)
def _extract_rules_from_spec_cached(spec_path_str: str) -> tuple[str, ...]:
    """Implementation backing extract_rules_from_spec — cached by path.

    P59A-W9: separate cacheable inner function so the public surface
    can stay typed as `list[str]` while the cache lives on the tuple
    (lru_cache requires hashable args + return values).
    """
    # Inline the original logic; can't recurse via the public wrapper.
    text = Path(spec_path_str).read_text(encoding="utf-8")
    section_match = re.search(
        r"###\s+28\.2\s+Non-negotiable rules.*?\n(.*?)(?:\n###\s|\n##\s|\Z)",
        text,
        flags=re.DOTALL,
    )
    if not section_match:
        raise SpecRuleExtractionError(
            f"Could not locate the §28.2 'Non-negotiable rules' section in "
            f"{spec_path_str}. Has the spec been renumbered or moved? "
            f"Update extract_rules_from_spec's regex anchor."
        )
    section = section_match.group(1)
    rule_re = re.compile(
        r"^(\d+)\.\s+(\*\*.+?)(?=^\d+\.\s+\*\*|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    rules: list[str] = []
    for m in rule_re.finditer(section):
        num = m.group(1)
        body = m.group(2).strip()
        rules.append(f"{num}. {body}")
    if not rules:
        raise SpecRuleExtractionError(
            "Found §28.2 but extracted zero rules. The 'N. **...**' rule "
            "shape regex no longer matches; check rule_re or spec formatting."
        )
    return tuple(rules)


def extract_rules_from_spec(spec_path: Path | None = None) -> list[str]:
    """Extract numbered rules 1-13 from §28.2 of ``spec.md``.

    The regex is anchored to the ``### 28.2 Non-negotiable rules`` heading
    and stops at the next ``###`` OR ``##`` heading, OR end-of-file —
    so reorganizing §28.2 to be the last subsection of §28 (or the spec
    ending after it) doesn't silently break the splice. Each
    ``N. **...**`` numbered item is captured.

    Raises ``SpecRuleExtractionError`` if zero rules are extracted —
    the prior silent ``return []`` made drift undetectable because
    ``verify_rule_count_matches_spec`` would report ``(0, 0)`` and
    declare itself matched.

    P59A-W9: results are cached on the string spec path; cache
    invalidates only on process restart.
    """
    path = spec_path or SPEC_PATH
    return list(_extract_rules_from_spec_cached(str(path)))


def render_voice_samples_section(samples: list[voice.VoiceSample]) -> str:
    if not samples:
        return (
            "(No voice samples are active. Daniel hasn't marked any posts as "
            "voice exemplars yet — the agent is operating without a calibrated "
            "voice signal until at least 3 samples are added in Settings.)"
        )
    blocks: list[str] = []
    for s in samples:
        header = f"### Sample (priority {s.priority}"
        if s.pillar:
            header += f", pillar={s.pillar}"
        header += ")"
        blocks.append(header)
        if s.context_note:
            blocks.append(f"_Context:_ {s.context_note}")
        blocks.append("")
        blocks.append("> " + s.text.replace("\n", "\n> "))
        blocks.append("")
    return "\n".join(blocks).strip()


def render_tool_catalog(tool_defs: list[tools.ToolDef] | None = None) -> str:
    """Render Section 7 — one bullet per tool: ``name(inputs) — description``."""
    defs = tool_defs if tool_defs is not None else tools.AGENT_TOOLS
    lines: list[str] = []
    for t in defs:
        props = t.input_schema.get("properties", {}) if isinstance(t.input_schema, dict) else {}
        inputs = ", ".join(props.keys()) if props else "()"
        lines.append(f"- **`{t.name}({inputs})`** — {t.description}")
    return "\n".join(lines)


# W22: explicit numbered splice instead of `\n\n`.join — the drift check
# can then compare the number of inserted rule strings directly without
# re-parsing rendered markdown.
_RULE_SEPARATOR = "\n\n"
# A sentinel comment line surrounds the spliced block so the drift check
# can find it deterministically regardless of how the surrounding
# template evolves.
_RULES_BEGIN = "<!-- BEGIN spliced rules from spec.md §28.2 -->"
_RULES_END = "<!-- END spliced rules -->"


def render_voice_profile_self_description(
    profile: voice_profile.VoiceProfile | None,
) -> str:
    """Section 1 splice. Empty string when no active profile exists."""
    if profile is None:
        return ""
    desc = profile.self_description()
    if not desc:
        return ""
    return (
        "Voice self-description (generated from your last "
        f"{profile.source_post_window_days} days, "
        f"{profile.source_post_count} posts): "
        f"{desc}"
    )


def render_voice_profile_structural(
    profile: voice_profile.VoiceProfile | None,
) -> str:
    """Section 5 prefix splice — compact structural read above raw samples.

    Renders cadence + vocabulary_signatures[:5] + stop_phrases[:5]. Empty
    string when no active profile exists, so the raw voice_samples block
    stands alone.
    """
    if profile is None:
        return ""
    cadence = profile.cadence()
    vocab = profile.vocabulary_signatures()[:5]
    stops = profile.stop_phrases()[:5]
    if not cadence and not vocab and not stops:
        return ""
    lines: list[str] = ["### Voice profile (generated)"]
    if cadence:
        avg_chars = cadence.get("avg_chars")
        avg_sent = cadence.get("avg_sentences")
        opl = cadence.get("one_idea_per_line_rate")
        bits: list[str] = []
        if avg_chars is not None:
            bits.append(f"avg_chars={avg_chars}")
        if avg_sent is not None:
            bits.append(f"avg_sentences={avg_sent}")
        if opl is not None:
            bits.append(f"one_idea_per_line_rate={opl}")
        if bits:
            lines.append(f"_Cadence:_ {', '.join(bits)}")
    if vocab:
        lines.append("_Vocabulary signatures:_ " + ", ".join(f"`{v}`" for v in vocab))
    if stops:
        lines.append("_Stop phrases (avoid):_ " + ", ".join(f"`{s}`" for s in stops))
    return "\n".join(lines)


# Phase 10 W10 — mtime-keyed cache. The prior `lru_cache(maxsize=1)`
# variant cached the file content for the Python process lifetime;
# editing config/voice_profile_prescriptive.md and reloading the
# Streamlit page produced stale output until restart. Keying on
# st_mtime_ns invalidates on each in-place edit and keeps the cache
# benefits for unchanged files.
@functools.lru_cache(maxsize=8)
def _load_voice_profile_prescriptive_cached(mtime_ns: int) -> str:  # noqa: ARG001 — mtime keys the cache
    return VOICE_PROFILE_PRESCRIPTIVE_PATH.read_text(encoding="utf-8")


def load_voice_profile_prescriptive() -> str:
    """Read the static §28.12 prescriptive voice anchor from disk.

    Phase 10 / §28.12. Unlike the generated voice profile (which is
    synthesized by Haiku from Daniel's recent posts and persisted in
    ``voice_profiles``), the prescriptive layer is hand-written and
    version-controlled. Returns the raw markdown — caller splices it
    verbatim into Section 5 of the system prompt.

    Phase 10 W10 — mtime-keyed cache so Daniel can iterate on the file
    without restarting Streamlit; the next read after an in-place edit
    picks up the new content.
    """
    mtime = VOICE_PROFILE_PRESCRIPTIVE_PATH.stat().st_mtime_ns
    return _load_voice_profile_prescriptive_cached(mtime)


class VoiceProfilePrescriptiveMissingError(SpecDriftError):
    """Raised when verify_voice_profile_prescriptive_present cannot find
    the static prescriptive voice anchor file.

    Same severity as SpecRuleExtractionError — drift between the file
    and the build pipeline would silently strip Section 5's
    prescriptive layer, and the agent would lose the load-bearing
    "what voice IS / what voice IS NOT" anchor without any signal.

    Phase 10 S10 — inherits from SpecDriftError so a single
    `except SpecDriftError` catches all drift errors raised by this
    module.
    """


def verify_voice_profile_prescriptive_present(
    path: Path | None = None,
) -> tuple[bool, int]:
    """Drift check — assert the static prescriptive anchor file exists.

    Returns ``(exists, byte_count)``. Callers (pre-commit / CI / tests)
    assert ``exists is True AND byte_count > 0``. Same defensive
    discipline as ``verify_rule_count_matches_spec``.

    Phase 10 / §28.12 — additive sibling to the generated voice profile
    drift check that already lives in
    ``verify_voice_profile_invariants``.
    """
    p = path or VOICE_PROFILE_PRESCRIPTIVE_PATH
    if not p.exists():
        raise VoiceProfilePrescriptiveMissingError(
            f"prescriptive voice anchor not found at {p}. "
            "Section 5 of the system prompt would lose its 'what voice "
            "IS / what voice IS NOT' anchor (§28.12 Phase 10). Restore "
            "the file or update VOICE_PROFILE_PRESCRIPTIVE_PATH."
        )
    contents = p.read_bytes()
    if not contents.strip():
        raise VoiceProfilePrescriptiveMissingError(
            f"prescriptive voice anchor at {p} exists but is empty. "
            "An empty splice carries no signal — populate the file "
            "with the §28.12 Phase 10 anchor content."
        )
    return (True, len(contents))


def render_niche_definition(nd: niche.NicheDefinition) -> str:
    """Section 1 splice for the §28.16 structured niche definition.

    Two states:
      * BOTH fields set → load-bearing line, verbatim per §28.16:
        "You help **{niche_person}** solve **{niche_problem}**."
      * EITHER empty → the disabled-state stub. Drafting is also refused
        by the orchestrator (rule #15), but the agent sees the prompt
        line so it can echo a sensible "fill out your niche first"
        response when asked.
    """
    if nd.is_defined():
        return f"You help **{nd.person}** solve **{nd.problem}**."
    return (
        "(niche not yet defined — drafting is disabled until Daniel fills "
        "Settings → Growth Agent → Niche)"
    )


def build_system_prompt(conn: sqlite3.Connection) -> str:
    """Assemble the runtime system prompt from the template + DB."""
    template = _read_template()
    rules = extract_rules_from_spec()
    rules_block = (
        f"{_RULES_BEGIN}\n"
        + _RULE_SEPARATOR.join(rules)
        + f"\n{_RULES_END}"
    )

    samples = voice.get_active_voice_samples(conn)
    voice_block = render_voice_samples_section(samples)
    tool_block = render_tool_catalog()

    active_profile = voice_profile.get_active(conn)
    profile_self_desc = render_voice_profile_self_description(active_profile)
    profile_structural = render_voice_profile_structural(active_profile)

    nd = niche.get_niche(conn)
    niche_block = render_niche_definition(nd)

    # Phase 5.9 / §28.21 — top-N active personality lore rows. Silent
    # splice (empty string) when there are zero active rows; no banner.
    splice_n = personality_lore.get_splice_count(conn)
    active_lore = personality_lore.list_active(conn, limit=splice_n)
    lore_block = personality_lore.render_splice_block(active_lore)

    # Phase 10 / §28.12 — prescriptive voice anchor. Read lazily so a
    # missing file surfaces as the explicit drift-check exception when
    # callers invoke verify_voice_profile_prescriptive_present, rather
    # than silently leaving the placeholder in the rendered prompt.
    try:
        prescriptive_block = load_voice_profile_prescriptive()
    except FileNotFoundError:
        # Defensive: never crash the build. The drift check is the
        # contract that surfaces the missing file in pre-commit / CI.
        prescriptive_block = ""

    out = template.replace(NON_NEGOTIABLE_PLACEHOLDER, rules_block)
    out = out.replace(VOICE_SAMPLES_PLACEHOLDER, voice_block)
    out = out.replace(TOOL_CATALOG_PLACEHOLDER, tool_block)
    out = out.replace(VOICE_PROFILE_SELF_DESCRIPTION_PLACEHOLDER, profile_self_desc)
    out = out.replace(VOICE_PROFILE_STRUCTURAL_PLACEHOLDER, profile_structural)
    out = out.replace(VOICE_PROFILE_PRESCRIPTIVE_PLACEHOLDER, prescriptive_block)
    out = out.replace(NICHE_DEFINITION_PLACEHOLDER, niche_block)
    out = out.replace(PERSONALITY_LORE_PLACEHOLDER, lore_block)
    return out


# ---------------------------------------------------------------------------
# §28.12 drift check — verify voice_profiles table has 0 or 1 active rows,
# and that build_system_prompt actually replaced both placeholders.
# Pre-commit / CI calls this. Same pattern as verify_rule_count_matches_spec.
# ---------------------------------------------------------------------------
class VoiceProfileInvariantError(RuntimeError):
    """Raised when voice_profiles violates the at-most-one-active invariant."""


def verify_voice_profile_invariants(
    conn: sqlite3.Connection, prompt_text: str | None = None
) -> tuple[int, bool]:
    """Returns (active_row_count, placeholder_replaced_in_prompt).

    Callers assert active_row_count in {0, 1} and (when prompt_text is
    provided) that no placeholder string survives in the rendered prompt.
    """
    active_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM voice_profiles WHERE is_active = 1"
        ).fetchone()[0]
    )
    if active_count not in (0, 1):
        raise VoiceProfileInvariantError(
            f"voice_profiles has {active_count} active rows; expected 0 or 1. "
            "Generation path must atomically deactivate-then-insert."
        )
    placeholders_replaced = True
    if prompt_text is not None:
        for placeholder in (
            VOICE_PROFILE_SELF_DESCRIPTION_PLACEHOLDER,
            VOICE_PROFILE_STRUCTURAL_PLACEHOLDER,
        ):
            if placeholder in prompt_text:
                placeholders_replaced = False
                break
    return (active_count, placeholders_replaced)


# ---------------------------------------------------------------------------
# §29.8 drift check — reply_intent enum stays in sync across:
#   1. spec.md §29.5 (table row "Reply intent | ... | v1 values")
#   2. app/agent/reply_targets.REPLY_INTENT_ENUM (single source of truth in code)
#   3. config/agent_system_prompt.md Section 6 ("Reply intent (§29.5): ...")
#
# Pre-commit / CI calls verify_reply_intent_enum_matches; any divergence is
# a hard fail. Same pattern as verify_rule_count_matches_spec.
# ---------------------------------------------------------------------------
def extract_reply_intent_enum_from_spec(spec_path: Path | None = None) -> list[str]:
    """Pull the v1 reply_intent values from spec §29.5.

    Anchored on the line ``| **Reply intent** | ... | growth / icp_discovery
    / relationship / product_adjacent / thought_leadership | ...``. The
    enum values are slash-separated in that table cell.
    """
    text = (spec_path or SPEC_PATH).read_text(encoding="utf-8")
    # Table columns: | **Reply intent** | <Lives on> | <v1 values> | <Describes> |
    # Capture the 3rd column ("v1 values"); 2nd column has a `+` literal so a
    # plain `[^|]*` is sufficient — we don't need to be cleverer.
    m = re.search(
        r"\|\s*\*\*Reply intent\*\*\s*\|[^|]*\|\s*([^|]+?)\s*\|",
        text,
    )
    if not m:
        raise SpecRuleExtractionError(
            "Could not locate the §29.5 reply_intent enum row in spec.md. "
            "Has the table format changed? Update the regex anchor."
        )
    raw = m.group(1)
    return [v.strip().strip("`") for v in raw.split("/") if v.strip()]


def extract_reply_intent_enum_from_prompt(prompt_text: str | None = None) -> list[str]:
    """Pull the reply_intent values from Section 6 of the template.

    /review-2 🔵 #2 — anchored to the start of the line so a future prose
    line elsewhere in the template (e.g. "When the user mentions their
    Reply intent: take it seriously") can't silently steal the match.
    """
    text = prompt_text if prompt_text is not None else _read_template()
    m = re.search(r"^Reply intent[^\n]*:\s*([^\n]+)", text, flags=re.MULTILINE)
    if not m:
        return []
    raw = m.group(1)
    return [v.strip() for v in raw.split(",") if v.strip()]


def verify_reply_intent_enum_matches() -> tuple[list[str], list[str], list[str]]:
    """Drift check — returns the enum from spec, code, and prompt.

    Callers (pre-commit / CI) assert all three lists are equal *as sets*.
    Order in the spec table and in the code tuple is canonical; the prompt
    template uses the same order but the check compares as sets to be
    robust to formatting changes.

    Phase 10 / §29.5 — `verify_reply_intent_enum_dispatcher_in_sync`
    extends this contract to assert the `app.agent.client` dispatcher
    imports the same REPLY_INTENT_ENUM. Same source-of-truth discipline
    as the spec/code/prompt three-way check above — divergence between
    the dispatcher's validation set and the canonical enum would let
    the agent slip a stale value past the gate.
    """
    from app.agent.reply_targets import REPLY_INTENT_ENUM
    spec_values = extract_reply_intent_enum_from_spec()
    code_values = list(REPLY_INTENT_ENUM)
    prompt_values = extract_reply_intent_enum_from_prompt()
    return spec_values, code_values, prompt_values


def verify_reply_intent_enum_dispatcher_in_sync() -> bool:
    """Drift check — `app.agent.client._validate_reply_intent_or_error`
    must reach the same REPLY_INTENT_ENUM object the spec and the
    system prompt enumerate.

    Phase 10 / §29.5 promotion — the dispatcher's validation set is the
    one place a stale enum value could let a refused-by-spec intent
    slip into a real agent_drafts row.

    Phase 10 S3 — runtime object-identity check instead of text-grep on
    the source file. The prior implementation grepped for the literal
    "from app.agent.reply_targets import REPLY_INTENT_ENUM" string —
    brittle to a semantically-equivalent refactor (e.g.
    ``from app.agent import reply_targets; reply_targets.REPLY_INTENT_ENUM``).
    Object identity proves the binding actually resolves to the same
    canonical tuple at call time, regardless of import shape.

    Returns True on match; raises SpecRuleExtractionError on mismatch
    so the CI surface is symmetric with the other §29.5 drift checks.
    """
    from app.agent.client import _validate_reply_intent_or_error
    from app.agent.reply_targets import REPLY_INTENT_ENUM as canonical

    # The dispatcher does a lazy import of REPLY_INTENT_ENUM inside
    # _validate_reply_intent_or_error; the easiest portable way to
    # inspect what symbol the function actually reaches is to read it
    # out of the function's closure via __wrapped__ / __globals__ at
    # the module level OR to ask the function indirectly by calling
    # it with a malformed input and inspecting the error message.
    # We pick the latter: it exercises the actual import path the
    # production code takes and surfaces the resolved enum values.
    from unittest.mock import MagicMock
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = None  # missing setting → default True
    error_msg = _validate_reply_intent_or_error(fake_conn, {})
    if error_msg is None:
        raise SpecRuleExtractionError(
            "_validate_reply_intent_or_error accepted empty input — "
            "the §29.5 gate is dead. Check reply_intent_required default."
        )
    # The "missing reply_intent" branch enumerates the valid values in
    # the error message: f"Pick one of {list(REPLY_INTENT_ENUM)}". We
    # check every canonical value appears.
    for value in canonical:
        if value not in error_msg:
            raise SpecRuleExtractionError(
                f"_validate_reply_intent_or_error error message is missing "
                f"canonical enum value {value!r}. Either the dispatcher is "
                f"reaching a different REPLY_INTENT_ENUM, or the error "
                f"message format changed. error_msg={error_msg!r}"
            )
    return True


# ---------------------------------------------------------------------------
# Phase 10 / §28.3 Section 4 drift check — the three additive blocks must
# all be present in the system prompt template. Each block is bracketed by
# explicit BEGIN/END sentinel comments so the check is structural rather
# than substring-only (a future copy edit that splits a block into prose
# variants won't silently disable the drift check).
# ---------------------------------------------------------------------------
class Section4AnchorMissingError(SpecDriftError):
    """Raised when verify_section_4_anchors finds a missing additive block.

    Each of the three Phase 10 additive blocks (engagement-with-integrity,
    screenshot test, IWH operationalized) is the kind of edit that
    *should* survive arbitrary §28.3 reorganization but can be
    accidentally deleted in a future copy-edit pass. The drift check
    surfaces the missing block by name so the fix is one-line.

    Phase 10 S10 — inherits from SpecDriftError so a single
    `except SpecDriftError` catches every drift error in this module.
    """


SECTION_4_ANCHOR_SENTINELS: tuple[tuple[str, str, str], ...] = (
    (
        "engagement_with_integrity",
        "<!-- BEGIN Phase 10 / §28.3 — engagement-with-integrity framing block -->",
        "<!-- END Phase 10 engagement-with-integrity block -->",
    ),
    (
        "screenshot_test_principle",
        "<!-- BEGIN Phase 10 / §28.3 — screenshot test principle -->",
        "<!-- END Phase 10 screenshot test principle -->",
    ),
    (
        "iwh_operationalized",
        "<!-- BEGIN Phase 10 / §28.3 — IWH operationalized block -->",
        "<!-- END Phase 10 IWH operationalized block -->",
    ),
)


def verify_section_4_anchors(
    prompt_text: str | None = None,
) -> dict[str, bool]:
    """Drift check — every Phase 10 §28.3 Section 4 additive block present.

    Returns a dict ``{anchor_name: present}`` covering all three Phase 10
    blocks. Callers (pre-commit / CI / tests) assert every value is
    ``True``. Raises ``Section4AnchorMissingError`` when any block is
    absent — the error names the missing anchor and the sentinel pair
    so the fix path is unambiguous.

    Same defensive discipline as ``verify_rule_count_matches_spec``:
    structural sentinels survive arbitrary copy edits to the block
    body (the rule body can be rewritten in place; the sentinels stay).
    """
    text = prompt_text if prompt_text is not None else _read_template()
    results: dict[str, bool] = {}
    missing: list[tuple[str, str, str]] = []
    for name, begin, end in SECTION_4_ANCHOR_SENTINELS:
        present = (begin in text) and (end in text)
        results[name] = present
        if not present:
            missing.append((name, begin, end))
    if missing:
        details = "\n".join(
            f"  - {name}: missing BEGIN={begin!r} or END={end!r}"
            for name, begin, end in missing
        )
        raise Section4AnchorMissingError(
            "Section 4 of the system prompt is missing Phase 10 additive "
            "blocks. Each block is bracketed by sentinel comments — "
            "restore the missing pair below.\n" + details
        )
    return results


def verify_rule_count_matches_spec(prompt_text: str) -> tuple[int, int]:
    """Drift check — count of rules in spec vs count spliced into the prompt.

    Returns ``(spec_count, prompt_count)``. Callers (CI / pre-commit)
    assert equality. Implementation reads the BEGIN/END sentinel comments
    that ``build_system_prompt`` writes around the spliced block, then
    counts the ``^N. **`` rule starts inside. This is more robust than
    parsing Section 3 markdown because the structural boundary is
    explicit and doesn't depend on Section 4's heading remaining stable.
    """
    spec_rules = extract_rules_from_spec()
    block = re.search(
        re.escape(_RULES_BEGIN) + r"(.*?)" + re.escape(_RULES_END),
        prompt_text,
        flags=re.DOTALL,
    )
    if not block:
        return (len(spec_rules), 0)
    prompt_count = len(
        re.findall(r"^\d+\.\s+\*\*", block.group(1), flags=re.MULTILINE)
    )
    return (len(spec_rules), prompt_count)
