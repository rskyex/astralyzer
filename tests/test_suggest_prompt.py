"""Unit tests for prompt + tool-schema construction. No SDK required."""
from astralyzer.suggest.prompt import (
    SUBMIT_CODE_TOOL,
    build_system_prompt,
    build_user_prompt,
    codebook_block,
)


CODEBOOK_FIXTURE = [
    {
        "field": "operative_function",
        "definition": "Functional role of the operative term.",
        "decision_rules": "Pick the dominant function.",
        "examples": [],
        "value_domain": ["permits", "restricts", "other"],
    },
    {
        "field": "ai_operation_effect",
        "definition": "How AI alters the provision's operation.",
        "decision_rules": "TODO — fill before first adjudication.",
        "examples": [],
        "value_domain": None,
    },
]


def test_codebook_block_includes_fields_and_values():
    text = codebook_block(CODEBOOK_FIXTURE)
    assert "operative_function" in text
    assert "permits" in text and "restricts" in text
    assert "Functional role" in text


def test_codebook_block_omits_todo_decision_rules():
    text = codebook_block(CODEBOOK_FIXTURE)
    assert "Decision rules: Pick the dominant function." in text
    # The TODO placeholder should NOT appear as decision rules in the prompt.
    assert "Decision rules: TODO" not in text


def test_system_prompt_includes_non_negotiables():
    text = build_system_prompt(CODEBOOK_FIXTURE)
    assert "verbatim substring" in text
    assert "SUGGESTION" in text
    assert "confidence" in text.lower()
    assert "permits" in text  # codebook embedded


def test_user_prompt_includes_provision_text_and_anchor():
    doc = {"id": "ost-1967", "full_title": "Outer Space Treaty", "instrument_type": "space"}
    prov = {"id": "ost-1967:1", "citation_anchor": "Article I", "text": "The body."}
    text = build_user_prompt(doc, prov)
    assert "Article I" in text
    assert "The body." in text
    assert "ost-1967" in text


def test_submit_code_tool_schema_matches_vocab():
    schema = SUBMIT_CODE_TOOL["input_schema"]["properties"]
    # Controlled-vocab fields list the full enum.
    assert "permits" in schema["operative_function"]["enum"]
    assert "assigns_authority" in schema["operative_function"]["enum"]
    assert "operating_party" in schema["authority_default"]["enum"]
    assert "none" in schema["verification_mechanism"]["enum"]
    assert 0 in schema["independent_epistemic_access"]["enum"]
    assert 2 in schema["independent_epistemic_access"]["enum"]
    # Required fields force the model to produce a span, rationale, confidence.
    required = SUBMIT_CODE_TOOL["input_schema"]["required"]
    for f in ("source_span_ref", "rationale", "confidence", "coupling_domains"):
        assert f in required
