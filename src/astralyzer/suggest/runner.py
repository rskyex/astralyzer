"""Orchestrate suggestion runs.

Pick target provisions, call the provider, validate the payload, write
status='suggested' rows. Idempotent by default: skips provisions that
already have a suggestion from the chosen LLM coder. With regenerate=True,
existing suggestions from that coder are deleted first.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from astralyzer.db import open_conn
from astralyzer.review import repo as review_repo
from astralyzer.suggest.provider import Provider, SuggestionPayload


class SuggestionError(Exception):
    pass


@dataclass
class RunOutcome:
    provision_id: str
    status: str  # "suggested" | "skipped" | "failed" | "dry-run"
    detail: str = ""
    code_id: str | None = None


def _load_codebook(conn) -> list[dict]:
    rows = []
    for r in conn.execute(
        "SELECT field, definition, decision_rules, examples, value_domain, version "
        "FROM codebook_fields ORDER BY field"
    ):
        d = dict(r)
        for k in ("examples", "value_domain"):
            if d.get(k):
                d[k] = json.loads(d[k])
        rows.append(d)
    if not rows:
        raise SuggestionError(
            "codebook is empty. Run `astralyzer codebook load codebook/codebook.yaml`."
        )
    return rows


def _assert_llm_coder(conn, coder_id: str) -> dict:
    row = conn.execute(
        "SELECT id, is_human, model_ref FROM coders WHERE id = ?", (coder_id,)
    ).fetchone()
    if row is None:
        raise SuggestionError(f"no such coder: {coder_id!r}")
    if row["is_human"]:
        raise SuggestionError(
            f"coder {coder_id!r} is marked is_human=1; suggestions require an LLM coder"
        )
    return dict(row)


def _resolve_provisions(conn, *,
                        provision_id: str | None,
                        document_id: str | None,
                        all_: bool) -> list[dict]:
    if provision_id:
        rows = list(conn.execute(
            "SELECT id, document_id, citation_anchor, text "
            "FROM provisions WHERE id = ?", (provision_id,)))
    elif document_id:
        rows = list(conn.execute(
            "SELECT id, document_id, citation_anchor, text "
            "FROM provisions WHERE document_id = ? ORDER BY ordinal",
            (document_id,)))
    elif all_:
        rows = list(conn.execute(
            "SELECT id, document_id, citation_anchor, text "
            "FROM provisions ORDER BY document_id, ordinal"))
    else:
        raise SuggestionError(
            "specify --provision, --document, or --all"
        )
    return [dict(r) for r in rows]


_WS_RE = re.compile(r"\s+")


def _validate_span(provision_text: str, span: str) -> str | None:
    """Return the canonical (verbatim) span if it appears in provision_text,
    else None. Tolerates whitespace differences by canonicalizing both sides;
    on a normalized match, falls back to the model's span unchanged."""
    if not span:
        return None
    if span in provision_text:
        return span
    norm_text = _WS_RE.sub(" ", provision_text).strip()
    norm_span = _WS_RE.sub(" ", span).strip()
    if norm_span and norm_span in norm_text:
        # Match exists modulo whitespace; record what the model said.
        return span
    return None


def run_suggestions(
    *,
    provider: Provider,
    coder_id: str,
    provision_id: str | None = None,
    document_id: str | None = None,
    all_: bool = False,
    limit: int | None = None,
    regenerate: bool = False,
    dry_run: bool = False,
) -> list[RunOutcome]:
    outcomes: list[RunOutcome] = []
    with open_conn() as conn:
        coder = _assert_llm_coder(conn, coder_id)
        codebook = _load_codebook(conn)
        targets = _resolve_provisions(conn,
                                      provision_id=provision_id,
                                      document_id=document_id,
                                      all_=all_)

    # Process outside the open_conn so each provision gets its own short
    # transaction; long-running model calls shouldn't hold a write lock.
    n_done = 0
    for prov in targets:
        if limit is not None and n_done >= limit:
            break

        document = review_repo.get_document(prov["document_id"])
        if document is None:
            outcomes.append(RunOutcome(prov["id"], "failed", "document missing"))
            continue

        existing_id = _existing_suggestion_id(prov["id"], coder_id)
        if existing_id and not regenerate:
            outcomes.append(RunOutcome(prov["id"], "skipped",
                                       f"suggestion already exists ({existing_id})"))
            continue
        if existing_id and regenerate:
            with open_conn() as conn:
                conn.execute("DELETE FROM codes WHERE id = ?", (existing_id,))
                conn.commit()

        if dry_run:
            outcomes.append(RunOutcome(prov["id"], "dry-run", "would call provider"))
            n_done += 1
            continue

        try:
            payload = provider.suggest(document=document, provision=prov, codebook=codebook)
        except Exception as e:
            outcomes.append(RunOutcome(prov["id"], "failed", f"provider error: {e}"))
            continue

        verified = _validate_span(prov["text"], payload.source_span_ref)
        if verified is None:
            outcomes.append(RunOutcome(
                prov["id"], "failed",
                f"source_span_ref is not a verbatim substring of the provision; "
                f"got: {payload.source_span_ref!r}"
            ))
            continue
        payload.source_span_ref = verified

        code_id = uuid.uuid4().hex[:16]
        try:
            review_repo.create_code(
                code_id=code_id,
                provision_id=prov["id"],
                coder_id=coder_id,
                status="suggested",
                operative_term_raw=payload.operative_term_raw,
                term_id=None,
                operative_function=payload.operative_function,
                authority_default=payload.authority_default,
                verification_mechanism=payload.verification_mechanism,
                independent_epistemic_access=payload.independent_epistemic_access,
                ai_operation_effect=payload.ai_operation_effect,
                source_span_ref=payload.source_span_ref,
                rationale=f"[model: {provider.model_ref}] {payload.rationale}",
                coupling_domains=payload.coupling_domains,
            )
            with open_conn() as conn:
                conn.execute(
                    "UPDATE codes SET suggested_confidence = ? WHERE id = ?",
                    (payload.confidence, code_id),
                )
                conn.commit()
        except Exception as e:
            outcomes.append(RunOutcome(prov["id"], "failed", f"insert error: {e}"))
            continue

        outcomes.append(RunOutcome(prov["id"], "suggested", "", code_id=code_id))
        n_done += 1

    return outcomes


def _existing_suggestion_id(provision_id: str, coder_id: str) -> str | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT id FROM codes "
            "WHERE provision_id = ? AND coder_id = ? AND status = 'suggested' "
            "LIMIT 1",
            (provision_id, coder_id),
        ).fetchone()
        return row["id"] if row else None
