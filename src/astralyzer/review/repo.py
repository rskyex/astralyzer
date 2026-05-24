"""DB queries for the review UI. Thin wrappers over sqlite3."""
from __future__ import annotations

import sqlite3
from typing import Any

from astralyzer.db import open_conn


# Controlled vocabularies. Source of truth is the CHECK constraints in
# migrations/0001_initial_schema.sql; these must stay in sync. The codebook
# table holds editorial definitions, not the wire vocabulary.
VOCABS = {
    "operative_function": [
        "permits", "restricts", "triggers_notification", "defines_zone",
        "allocates_right", "assigns_authority", "other",
    ],
    "authority_default": [
        "operating_party", "launching_state", "consensus_body",
        "unspecified", "other",
    ],
    "verification_mechanism": [
        "none", "notification", "inspection",
        "third_party_monitoring", "data_sharing", "other",
    ],
    "independent_epistemic_access": [0, 1, 2],
    "coupling_domains": ["space", "nuclear", "cyber", "ai"],
}


def list_documents() -> list[dict[str, Any]]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT d.id, d.short_name, d.full_title, d.instrument_type, "
            "       d.version_or_date, "
            "       (SELECT COUNT(*) FROM provisions p WHERE p.document_id = d.id) AS n_provisions, "
            "       (SELECT COUNT(*) FROM codes c JOIN provisions p ON c.provision_id = p.id "
            "        WHERE p.document_id = d.id AND c.status = 'adjudicated') AS n_adjudicated "
            "FROM documents d ORDER BY d.id"
        )]


def get_document(document_id: str) -> dict[str, Any] | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return dict(row) if row else None


def list_provisions(document_id: str) -> list[dict[str, Any]]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT p.*, "
            "  (SELECT COUNT(*) FROM codes c WHERE c.provision_id = p.id AND c.status='suggested') AS n_suggested, "
            "  (SELECT COUNT(*) FROM codes c WHERE c.provision_id = p.id AND c.status='human_draft') AS n_draft, "
            "  (SELECT COUNT(*) FROM codes c WHERE c.provision_id = p.id AND c.status='adjudicated') AS n_adjudicated "
            "FROM provisions p WHERE p.document_id = ? ORDER BY p.ordinal",
            (document_id,)
        )]


def get_provision(provision_id: str) -> dict[str, Any] | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT * FROM provisions WHERE id = ?", (provision_id,)
        ).fetchone()
        return dict(row) if row else None


def get_provision_neighbors(provision_id: str) -> dict[str, str | None]:
    """Return (prev_id, next_id) within the same document for nav."""
    with open_conn() as conn:
        p = conn.execute(
            "SELECT document_id, ordinal FROM provisions WHERE id = ?", (provision_id,)
        ).fetchone()
        if p is None:
            return {"prev": None, "next": None}
        prev = conn.execute(
            "SELECT id FROM provisions WHERE document_id = ? AND ordinal < ? "
            "ORDER BY ordinal DESC LIMIT 1", (p["document_id"], p["ordinal"])
        ).fetchone()
        nxt = conn.execute(
            "SELECT id FROM provisions WHERE document_id = ? AND ordinal > ? "
            "ORDER BY ordinal ASC LIMIT 1", (p["document_id"], p["ordinal"])
        ).fetchone()
        return {"prev": prev["id"] if prev else None,
                "next": nxt["id"] if nxt else None}


def list_codes_for_provision(provision_id: str) -> list[dict[str, Any]]:
    with open_conn() as conn:
        codes = [dict(r) for r in conn.execute(
            "SELECT c.*, co.name AS coder_name, co.is_human AS coder_is_human, "
            "       t.canonical_label AS term_label "
            "FROM codes c "
            "JOIN coders co ON c.coder_id = co.id "
            "LEFT JOIN terms t ON c.term_id = t.id "
            "WHERE c.provision_id = ? "
            "ORDER BY CASE c.status "
            "  WHEN 'adjudicated' THEN 0 WHEN 'human_draft' THEN 1 WHEN 'suggested' THEN 2 END, "
            "  c.created_at",
            (provision_id,)
        )]
        for c in codes:
            c["coupling_domains"] = [r["domain"] for r in conn.execute(
                "SELECT domain FROM code_coupling_domains WHERE code_id = ? ORDER BY domain",
                (c["id"],))]
        return codes


def get_code(code_id: str) -> dict[str, Any] | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT c.*, co.name AS coder_name, co.is_human AS coder_is_human, "
            "       t.canonical_label AS term_label "
            "FROM codes c "
            "JOIN coders co ON c.coder_id = co.id "
            "LEFT JOIN terms t ON c.term_id = t.id "
            "WHERE c.id = ?", (code_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["coupling_domains"] = [r["domain"] for r in conn.execute(
            "SELECT domain FROM code_coupling_domains WHERE code_id = ? ORDER BY domain",
            (code_id,)).fetchall()]
        return d


def list_coders() -> list[dict[str, Any]]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, role, is_human, model_ref FROM coders ORDER BY id"
        )]


def get_coder(handle: str) -> dict[str, Any] | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT id, name, role, is_human, model_ref FROM coders WHERE id = ?",
            (handle,)
        ).fetchone()
        return dict(row) if row else None


def list_terms() -> list[dict[str, Any]]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, canonical_label, definition FROM terms ORDER BY canonical_label"
        )]


def create_term(term_id: str, canonical_label: str, created_by: str,
                definition: str | None = None) -> None:
    with open_conn() as conn:
        conn.execute(
            "INSERT INTO terms(id, canonical_label, definition, created_by) "
            "VALUES (?, ?, ?, ?)",
            (term_id, canonical_label, definition, created_by),
        )
        conn.commit()


def find_term_by_label(label: str) -> dict[str, Any] | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT id, canonical_label FROM terms WHERE canonical_label = ?",
            (label.strip(),)
        ).fetchone()
        return dict(row) if row else None


def create_code(
    *,
    code_id: str,
    provision_id: str,
    coder_id: str,
    status: str,
    operative_term_raw: str | None = None,
    term_id: str | None = None,
    operative_function: str | None = None,
    authority_default: str | None = None,
    verification_mechanism: str | None = None,
    independent_epistemic_access: int | None = None,
    ai_operation_effect: str | None = None,
    source_span_ref: str,
    rationale: str | None = None,
    coupling_domains: list[str] | None = None,
) -> None:
    with open_conn() as conn:
        conn.execute(
            "INSERT INTO codes("
            "id, provision_id, coder_id, status, operative_term_raw, term_id, "
            "operative_function, authority_default, verification_mechanism, "
            "independent_epistemic_access, ai_operation_effect, source_span_ref, rationale) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code_id, provision_id, coder_id, status,
             operative_term_raw or None, term_id,
             operative_function or None, authority_default or None,
             verification_mechanism or None, independent_epistemic_access,
             ai_operation_effect or None, source_span_ref,
             rationale or None),
        )
        for d in coupling_domains or []:
            conn.execute(
                "INSERT INTO code_coupling_domains(code_id, domain) VALUES (?, ?)",
                (code_id, d),
            )
        conn.commit()


def update_code(
    code_id: str,
    *,
    operative_term_raw: str | None = None,
    term_id: str | None = None,
    operative_function: str | None = None,
    authority_default: str | None = None,
    verification_mechanism: str | None = None,
    independent_epistemic_access: int | None = None,
    ai_operation_effect: str | None = None,
    source_span_ref: str | None = None,
    rationale: str | None = None,
    coupling_domains: list[str] | None = None,
    status: str | None = None,
) -> None:
    with open_conn() as conn:
        conn.execute(
            "UPDATE codes SET "
            "  operative_term_raw=?, term_id=?, operative_function=?, "
            "  authority_default=?, verification_mechanism=?, "
            "  independent_epistemic_access=?, ai_operation_effect=?, "
            "  source_span_ref=COALESCE(?, source_span_ref), rationale=?, "
            "  status=COALESCE(?, status) "
            "WHERE id=?",
            (operative_term_raw or None, term_id,
             operative_function or None, authority_default or None,
             verification_mechanism or None, independent_epistemic_access,
             ai_operation_effect or None, source_span_ref, rationale or None,
             status, code_id),
        )
        if coupling_domains is not None:
            conn.execute("DELETE FROM code_coupling_domains WHERE code_id = ?", (code_id,))
            for d in coupling_domains:
                conn.execute(
                    "INSERT INTO code_coupling_domains(code_id, domain) VALUES (?, ?)",
                    (code_id, d),
                )
        conn.commit()


def delete_code(code_id: str) -> None:
    with open_conn() as conn:
        conn.execute("DELETE FROM codes WHERE id = ?", (code_id,))
        conn.commit()


def adjudicate_code(code_id: str) -> None:
    """Set status to 'adjudicated'. The DB enforces one-per-provision and the
    canonical-term requirement; sqlite3.IntegrityError surfaces to the route."""
    with open_conn() as conn:
        conn.execute("UPDATE codes SET status = 'adjudicated' WHERE id = ?", (code_id,))
        conn.commit()
