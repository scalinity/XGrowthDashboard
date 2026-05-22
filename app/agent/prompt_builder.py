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


def build_system_prompt(conn: sqlite3.Connection) -> str:
    """Assemble the runtime system prompt from the template + DB."""
    template = _read_template()
    rules = extract_rules_from_spec()
    rules_block = "\n\n".join(rules) if rules else "(rule splice failed — see prompt_builder.py)"

    samples = voice.get_active_voice_samples(conn)
    voice_block = render_voice_samples_section(samples)
    tool_block = render_tool_catalog()

    out = template.replace(NON_NEGOTIABLE_PLACEHOLDER, rules_block)
    out = out.replace(VOICE_SAMPLES_PLACEHOLDER, voice_block)
    out = out.replace(TOOL_CATALOG_PLACEHOLDER, tool_block)
    return out


def verify_rule_count_matches_spec(prompt_text: str) -> tuple[int, int]:
    """Drift check: how many rules in spec vs how many in the prompt.

    Returns ``(spec_count, prompt_count)``. Callers (CI / pre-commit) should
    assert equality. Mismatch means a rule was added/removed in spec without
    rerunning the build, OR the template was edited to drop a rule —
    either way, an explicit failure beats silent drift.
    """
    spec_rules = extract_rules_from_spec()
    # In the prompt, rules appear as "N. **..." lines under Section 3.
    section_3 = re.search(
        r"#\s+Section\s+3\b(.*?)(?=^#\s+Section\s+4\b)",
        prompt_text,
        flags=re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    if not section_3:
        return (len(spec_rules), 0)
    prompt_count = len(
        re.findall(r"^\d+\.\s+\*\*", section_3.group(1), flags=re.MULTILINE)
    )
    return (len(spec_rules), prompt_count)
