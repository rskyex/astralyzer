"""Anthropic provider.

Lazy-imports the anthropic SDK so the core CLI works without it installed.
Uses the Messages API with tool_use forced to submit_code, and prompt caching
on the codebook/system block (the same codebook is reused across every
suggestion in a batch).

API key: read from the ANTHROPIC_API_KEY env var. NEVER passed as a flag.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from astralyzer.suggest.prompt import SUBMIT_CODE_TOOL, build_system_prompt, build_user_prompt
from astralyzer.suggest.provider import SuggestionPayload


class AnthropicNotInstalled(RuntimeError):
    pass


def _load_sdk():
    try:
        import anthropic
    except ImportError as e:
        raise AnthropicNotInstalled(
            "anthropic SDK not installed. Install with: pip install -e \".[suggest]\""
        ) from e
    return anthropic


@dataclass
class AnthropicProvider:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024

    @property
    def model_ref(self) -> str:
        return f"anthropic:{self.model}"

    def __post_init__(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Export it in your environment "
                "(do not pass keys via CLI flags)."
            )
        anthropic = _load_sdk()
        self._client = anthropic.Anthropic()

    def suggest(self, *, document: dict, provision: dict, codebook: list[dict]) -> SuggestionPayload:
        system_text = build_system_prompt(codebook)
        user_text = build_user_prompt(document, provision)

        # Prompt caching on the codebook/system block: it's reused across
        # every provision in a batch, so we mark it cache-eligible.
        system_blocks = [
            {"type": "text", "text": system_text,
             "cache_control": {"type": "ephemeral"}}
        ]

        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_blocks,
            tools=[SUBMIT_CODE_TOOL],
            tool_choice={"type": "tool", "name": "submit_code"},
            messages=[{"role": "user", "content": user_text}],
        )

        tool_use = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"
             and getattr(b, "name", None) == "submit_code"),
            None,
        )
        if tool_use is None:
            raise RuntimeError(
                f"model did not call submit_code; got: {_compact_content(resp.content)}"
            )

        args = tool_use.input
        return SuggestionPayload(
            operative_term_raw=args.get("operative_term_raw") or None,
            operative_function=args.get("operative_function"),
            authority_default=args.get("authority_default"),
            verification_mechanism=args.get("verification_mechanism"),
            independent_epistemic_access=args.get("independent_epistemic_access"),
            ai_operation_effect=args.get("ai_operation_effect") or None,
            coupling_domains=list(args.get("coupling_domains") or []),
            source_span_ref=args["source_span_ref"],
            rationale=args["rationale"],
            confidence=float(args["confidence"]),
        )


def _compact_content(blocks) -> str:
    """Render a model response's content blocks for an error message, capped."""
    parts = []
    for b in blocks:
        if getattr(b, "type", None) == "text":
            parts.append(b.text[:200])
        else:
            parts.append(f"<{getattr(b, 'type', '?')}>")
    return json.dumps(parts)[:400]
