"""Provider interface for the suggestion layer.

A Provider takes a (document, provision, codebook) tuple and returns a
SuggestionPayload — the structured proposal that becomes a row in the codes
table with status='suggested'. The runner validates the payload, attaches
provenance (coder_id, model_ref), and writes it.

Concrete providers ship in sibling modules. They lazy-import their SDKs so
the core CLI works without optional dependencies installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SuggestionPayload:
    operative_term_raw: str | None = None
    operative_function: str | None = None
    authority_default: str | None = None
    verification_mechanism: str | None = None
    independent_epistemic_access: int | None = None
    ai_operation_effect: str | None = None
    coupling_domains: list[str] = field(default_factory=list)
    source_span_ref: str = ""
    rationale: str = ""
    confidence: float = 0.0


class Provider(Protocol):
    """A suggestion provider. Implementations must declare their model_ref so
    the runner can stamp it on the coder row (and on the suggestion's audit
    trail in the rationale)."""

    model_ref: str

    def suggest(
        self,
        *,
        document: dict,
        provision: dict,
        codebook: list[dict],
    ) -> SuggestionPayload: ...


def build_provider(name: str, model: str | None = None) -> Provider:
    """Factory: return a configured provider by name.

    'anthropic' requires the anthropic SDK; install with `pip install -e ".[suggest]"`.
    'mock' has no dependencies and is deterministic — used for tests and offline runs.
    """
    if name == "mock":
        from astralyzer.suggest.mock import MockProvider
        return MockProvider(model_ref=model or "mock:v1")
    if name == "anthropic":
        from astralyzer.suggest.anthropic import AnthropicProvider
        return AnthropicProvider(model=model or "claude-sonnet-4-6")
    raise ValueError(f"unknown provider: {name!r} (expected: anthropic, mock)")
