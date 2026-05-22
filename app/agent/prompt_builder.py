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

import re
import sqlite3
from pathlib import Path

from app.agent import tools, voice

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
PROMPT_TEMPLATE_PATH: Path = PROJECT_ROOT / "config" / "agent_system_prompt.md"
SPEC_PATH: Path = PROJECT_ROOT / "spec.md"

NON_NEGOTIABLE_PLACEHOLDER = "<!-- {{ NON_NEGOTIABLE_RULES_PLACEHOLDER }} -->"
VOICE_SAMPLES_PLACEHOLDER = "<!-- {{ VOICE_SAMPLES_PLACEHOLDER }} -->"
TOOL_CATALOG_PLACEHOLDER = "<!-- {{ TOOL_CATALOG_PLACEHOLDER }} -->"


def _read_template() -> str:
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


class SpecRuleExtractionError(RuntimeError):
    """Raised when extract_rules_from_spec cannot find any rules.

    Treat as a hard build failure — the assembled system prompt would
    otherwise carry the literal '(rule splice failed — see prompt_builder.py)'
    placeholder string in place of Section 3, and the agent would lose
    the non-negotiable rules silently.
    """


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
    """
    text = (spec_path or SPEC_PATH).read_text(encoding="utf-8")
    section_match = re.search(
        r"###\s+28\.2\s+Non-negotiable rules.*?\n(.*?)(?:\n###\s|\n##\s|\Z)",
        text,
        flags=re.DOTALL,
    )
    if not section_match:
        raise SpecRuleExtractionError(
            f"Could not locate the §28.2 'Non-negotiable rules' section in "
            f"{spec_path or SPEC_PATH}. Has the spec been renumbered or "
            f"moved? Update extract_rules_from_spec's regex anchor."
        )
    section = section_match.group(1)
    # Match "1. **...**" through end-of-paragraph (blank line OR next number).
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
    return rules


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

    out = template.replace(NON_NEGOTIABLE_PLACEHOLDER, rules_block)
    out = out.replace(VOICE_SAMPLES_PLACEHOLDER, voice_block)
    out = out.replace(TOOL_CATALOG_PLACEHOLDER, tool_block)
    return out


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
    """Pull the reply_intent values from Section 6 of the template."""
    text = prompt_text if prompt_text is not None else _read_template()
    m = re.search(r"Reply intent[^\n]*:\s*([^\n]+)", text)
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
    """
    from app.agent.reply_targets import REPLY_INTENT_ENUM
    spec_values = extract_reply_intent_enum_from_spec()
    code_values = list(REPLY_INTENT_ENUM)
    prompt_values = extract_reply_intent_enum_from_prompt()
    return spec_values, code_values, prompt_values


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
