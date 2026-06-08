"""Schema-level invariant tests.

Every test here corresponds to a methodological constraint named in
docs/methodology.md. If you change a constraint, change both.
"""
import sqlite3

import pytest

from astralyzer import db

SHA = "0" * 64


def _seed_minimal(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO documents("
        "id, short_name, full_title, instrument_type, official_source_url, "
        "version_or_date, retrieval_date, raw_text_ref, raw_text_sha256) "
        "VALUES('ost-1967','OST','Outer Space Treaty','space',"
        "'https://example.org/ost','1967-01-27','2026-05-24',"
        "'data/raw/ost-1967.txt', ?)",
        (SHA,),
    )
    conn.execute(
        "INSERT INTO provisions(id, document_id, ordinal, citation_anchor, "
        "text, char_start, char_end) "
        "VALUES('ost-1967:1','ost-1967',1,'Art. I','sample text',0,11)"
    )
    conn.execute(
        "INSERT INTO coders(id, name, role, is_human) "
        "VALUES('rs','R. Sky','lead',1)"
    )
    conn.execute(
        "INSERT INTO coders(id, name, role, is_human, model_ref) "
        "VALUES('llm-c','Claude Suggester','llm',0,'anthropic:claude-opus-4-7')"
    )
    conn.commit()


def test_one_adjudicated_per_provision(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
            "VALUES('c1','ost-1967:1','rs','adjudicated','quoted span')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
                "VALUES('c2','ost-1967:1','rs','adjudicated','quoted span')"
            )
            conn.commit()


def test_llm_coder_cannot_adjudicate(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
                "VALUES('c1','ost-1967:1','llm-c','adjudicated','q')"
            )
            conn.commit()


def test_llm_coder_cannot_draft(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
                "VALUES('c1','ost-1967:1','llm-c','human_draft','q')"
            )
            conn.commit()


def test_llm_suggestions_are_allowed(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref, "
            "suggested_confidence) "
            "VALUES('c1','ost-1967:1','llm-c','suggested','q',0.82)"
        )
        conn.commit()
        row = conn.execute("SELECT status FROM codes WHERE id='c1'").fetchone()
        assert row["status"] == "suggested"


def test_llm_cannot_be_promoted_via_update(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
            "VALUES('c1','ost-1967:1','llm-c','suggested','q')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE codes SET status='adjudicated' WHERE id='c1'")
            conn.commit()


def test_controlled_vocab_rejects_typo(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref, "
                "operative_function) "
                "VALUES('c1','ost-1967:1','rs','human_draft','q','permmits')"
            )
            conn.commit()


def test_coupling_domain_vocab_enforced(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
            "VALUES('c1','ost-1967:1','rs','human_draft','q')"
        )
        conn.execute(
            "INSERT INTO code_coupling_domains(code_id, domain) VALUES('c1','space')"
        )
        conn.execute(
            "INSERT INTO code_coupling_domains(code_id, domain) VALUES('c1','ai')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO code_coupling_domains(code_id, domain) "
                "VALUES('c1','quantum')"
            )
            conn.commit()


def test_coder_handle_is_immutable(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE coders SET id='rs2' WHERE id='rs'")
            conn.commit()


def test_dataset_version_is_write_once(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        conn.execute(
            "INSERT INTO dataset_versions(version, n_documents, n_provisions, "
            "n_adjudicated_codes, manifest_json) "
            "VALUES('0.1.0', 1, 1, 0, '{}')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO dataset_versions(version, n_documents, n_provisions, "
                "n_adjudicated_codes, manifest_json) "
                "VALUES('0.1.0', 1, 1, 0, '{}')"
            )
            conn.commit()


def test_reliability_run_rejects_same_coder(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reliability_runs(id, coder_a_id, coder_b_id, "
                "sample_provisions) VALUES('r1','rs','rs','[]')"
            )
            conn.commit()


def test_adjudicated_requires_canonical_term(tmp_db):
    """If a draft has a raw term, adjudicating it requires assigning term_id."""
    with db.open_conn() as conn:
        _seed_minimal(conn)
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref, "
            "operative_term_raw) "
            "VALUES('c1','ost-1967:1','rs','human_draft','q','peaceful purposes')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE codes SET status='adjudicated' WHERE id='c1'")
            conn.commit()

        # Canonicalize, then adjudication succeeds.
        conn.execute(
            "INSERT INTO terms(id, canonical_label) VALUES('peaceful-purposes','peaceful purposes')"
        )
        conn.execute(
            "UPDATE codes SET term_id='peaceful-purposes', status='adjudicated' WHERE id='c1'"
        )
        conn.commit()
        row = conn.execute("SELECT status, term_id FROM codes WHERE id='c1'").fetchone()
        assert row["status"] == "adjudicated"
        assert row["term_id"] == "peaceful-purposes"


def test_confidence_only_on_suggestions(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref, "
                "suggested_confidence) "
                "VALUES('c1','ost-1967:1','rs','human_draft','q',0.5)"
            )
            conn.commit()


def test_provision_offsets_are_ordered(tmp_db):
    with db.open_conn() as conn:
        _seed_minimal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO provisions(id, document_id, ordinal, citation_anchor, "
                "text, char_start, char_end) "
                "VALUES('ost-1967:2','ost-1967',2,'Art. II','x',10,5)"
            )
            conn.commit()
