"""Prompt + tool-schema construction for the suggestion layer.

The tool schema mirrors the schema CHECK constraints in
migrations/0001_initial_schema.sql; the model is forced to call the
submit_code tool, so the structured fields come back validated by the
provider SDK before they hit our DB.
"""
from __future__ import annotations

from astralyzer.review.repo import VOCABS


def codebook_block(codebook: list[dict]) -> str:
    """Format the codebook table into prompt-ready text."""
    lines = []
    for f in codebook:
        lines.append(f"### {f['field']}")
        lines.append((f.get("definition") or "").strip())
        rules = (f.get("decision_rules") or "").strip()
        if rules and not rules.lower().startswith("todo"):
            lines.append(f"Decision rules: {rules}")
        vd = f.get("value_domain")
        if vd:
            import json
            if isinstance(vd, str):
                vd = json.loads(vd)
            lines.append(f"Allowed values: {vd}")
        lines.append("")
    return "\n".join(lines).rstrip()


SYSTEM_PROMPT = """\
You are an analyst helping code governance instruments for the Interpretive
Authority Mapping Engine — a research dataset measuring how undefined
operative terms allocate interpretive authority across space, nuclear, cyber,
and AI governance instruments.

Your output is a SUGGESTION, never a gold record. A human will review every
suggestion before it enters the dataset. Be conservative: it is better to
leave a field empty than to guess.

Three non-negotiable rules:

1. `source_span_ref` MUST be a verbatim substring of the provision text
   provided in the user message. If you cannot identify a span that grounds
   your reading, leave the structured fields empty and explain in rationale.

2. Controlled-vocabulary fields (operative_function, authority_default,
   verification_mechanism, independent_epistemic_access, coupling_domains)
   must be chosen ONLY from the allowed values listed in the codebook below.
   If no listed value fits, choose 'other' and explain in rationale.

3. `confidence` is your own estimate, in [0, 1], of how likely a careful
   human coder would agree with your suggestion. Be calibrated, not generous.

Codebook fields:

{codebook}
"""


def build_system_prompt(codebook: list[dict]) -> str:
    return SYSTEM_PROMPT.format(codebook=codebook_block(codebook))


def build_user_prompt(document: dict, provision: dict) -> str:
    return (
        f"Document: {document['full_title']}\n"
        f"  id: {document['id']}\n"
        f"  instrument_type: {document['instrument_type']}\n"
        f"\n"
        f"Provision: {provision['citation_anchor']} (provision id: {provision['id']})\n"
        f"Text:\n"
        f'"""\n{provision["text"]}\n"""\n'
        f"\n"
        f"Submit a coded analysis via the submit_code tool. Remember:\n"
        f" - source_span_ref must be a verbatim substring of the provision text above.\n"
        f" - Use only the allowed values for controlled fields.\n"
        f" - Be conservative; empty fields are better than guesses."
    )


SUBMIT_CODE_TOOL = {
    "name": "submit_code",
    "description": (
        "Submit a coded analysis of the provision. Every populated field is a "
        "SUGGESTION subject to human review."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operative_term_raw": {
                "type": ["string", "null"],
                "description": (
                    "Verbatim capture of the undefined operative term from the provision "
                    "(e.g. 'peaceful purposes'). Null if no single term is salient."
                ),
            },
            "operative_function": {
                "type": ["string", "null"],
                "enum": [*VOCABS["operative_function"], None],
            },
            "authority_default": {
                "type": ["string", "null"],
                "enum": [*VOCABS["authority_default"], None],
            },
            "verification_mechanism": {
                "type": ["string", "null"],
                "enum": [*VOCABS["verification_mechanism"], None],
            },
            "independent_epistemic_access": {
                "type": ["integer", "null"],
                "enum": [0, 1, 2, None],
            },
            "ai_operation_effect": {
                "type": ["string", "null"],
                "description": (
                    "Free-text: how is operation altered when the operating party is, "
                    "or relies on, an AI system? Null if not applicable."
                ),
            },
            "coupling_domains": {
                "type": "array",
                "items": {"type": "string", "enum": VOCABS["coupling_domains"]},
                "uniqueItems": True,
                "description": "Governance domains this provision couples to.",
            },
            "source_span_ref": {
                "type": "string",
                "description": (
                    "VERBATIM substring of the provision text that grounds this code."
                ),
            },
            "rationale": {
                "type": "string",
                "description": "Brief justification, including any caveats.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "Self-estimate of how likely a careful human coder would agree."
                ),
            },
        },
        "required": ["source_span_ref", "rationale", "confidence", "coupling_domains"],
    },
}
