"""Deterministic mock provider for tests and offline runs.

Picks the first sentence of the provision as the source span and emits a
fixed (but plausible) coding so tests have stable assertions and the
suggestion path can be exercised without API keys.
"""
from __future__ import annotations

from dataclasses import dataclass

from astralyzer.suggest.provider import SuggestionPayload


@dataclass
class MockProvider:
    model_ref: str = "mock:v1"

    def suggest(self, *, document: dict, provision: dict, codebook: list[dict]) -> SuggestionPayload:
        text = provision["text"]
        # First sentence-ish, or first 120 chars, as the span.
        end = text.find(". ")
        span = text[: end + 1] if 0 < end < 200 else text[: min(120, len(text))]
        domain_map = {"space": ["space"], "nuclear": ["nuclear"], "cyber": ["cyber"],
                      "ai": ["ai"], "cross": ["space", "ai"]}
        return SuggestionPayload(
            operative_term_raw=None,
            operative_function="other",
            authority_default="unspecified",
            verification_mechanism="none",
            independent_epistemic_access=0,
            ai_operation_effect=None,
            coupling_domains=domain_map.get(document["instrument_type"], []),
            source_span_ref=span,
            rationale=f"(mock suggestion from {self.model_ref}; not a real LLM call)",
            confidence=0.5,
        )
