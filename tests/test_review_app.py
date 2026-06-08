"""Integration tests for the Flask review UI."""
import pytest

from astralyzer import db as db_mod
from astralyzer import ingest as ingest_mod
from astralyzer.review.app import create_app


DOC_TEXT = (
    "Article I\n"
    "First article body.\n\n"
    "Article II\n"
    "Second article body.\n"
)

META = dict(
    document_id="test-doc",
    short_name="TD",
    full_title="Test Doc",
    instrument_type="space",
    official_source_url="https://example.org",
    version_or_date="2026-01-01",
    retrieval_date="2026-05-24",
)


@pytest.fixture
def populated_db(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    ingest_mod.ingest_document(text=DOC_TEXT, **META)
    db_mod.add_coder("rs", name="R. Sky", role="lead", is_human=True, model_ref=None)
    db_mod.add_coder("llm", name="Claude", role="llm", is_human=False,
                     model_ref="anthropic:claude-opus-4-7")
    return tmp_path


@pytest.fixture
def client(populated_db):
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _login(client, handle: str):
    client.post("/set-coder", data={"coder": handle, "next": "/"})


def test_index_lists_documents(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"test-doc" in resp.data
    assert b"Test Doc" in resp.data


def test_document_view(client):
    resp = client.get("/documents/test-doc")
    assert resp.status_code == 200
    assert b"Article I" in resp.data
    assert b"Article II" in resp.data


def test_document_view_404(client):
    assert client.get("/documents/nope").status_code == 404


def test_provision_view(client):
    resp = client.get("/provisions/test-doc:1")
    assert resp.status_code == 200
    assert b"First article body" in resp.data


def test_create_code_requires_coder(client):
    resp = client.post("/provisions/test-doc:1/codes", data={})
    assert resp.status_code == 403


def test_create_code_rejects_llm_coder(client):
    _login(client, "llm")
    resp = client.post("/provisions/test-doc:1/codes", data={
        "source_span_ref": "First article body.",
    })
    assert resp.status_code == 403


def test_create_human_draft(client):
    _login(client, "rs")
    resp = client.post("/provisions/test-doc:1/codes", data={
        "operative_term_raw": "peaceful purposes",
        "operative_function": "permits",
        "authority_default": "operating_party",
        "source_span_ref": "First article body.",
        "coupling_domains": ["space", "ai"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    # Code appears on the provision view.
    assert b"peaceful purposes" in resp.data
    assert b"permits" in resp.data
    assert b"human_draft" in resp.data
    with db_mod.open_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM codes WHERE provision_id='test-doc:1'"
        ).fetchone()[0]
        assert n == 1
        domains = {r[0] for r in conn.execute(
            "SELECT domain FROM code_coupling_domains")}
        assert domains == {"space", "ai"}


def test_invalid_vocab_value_flashes_error(client):
    _login(client, "rs")
    resp = client.post("/provisions/test-doc:1/codes", data={
        "operative_function": "permmits",  # typo
        "source_span_ref": "x",
    }, follow_redirects=True)
    assert resp.status_code == 200
    # The CHECK constraint surfaces as a flashed error.
    assert b"could not create code" in resp.data


def test_adjudicate_draft_succeeds(client):
    _login(client, "rs")
    client.post("/provisions/test-doc:1/codes", data={
        "operative_function": "permits",
        "source_span_ref": "First article body.",
    })
    with db_mod.open_conn() as conn:
        code_id = conn.execute(
            "SELECT id FROM codes WHERE provision_id='test-doc:1'"
        ).fetchone()[0]
    resp = client.post(f"/codes/{code_id}/adjudicate", follow_redirects=True)
    assert resp.status_code == 200
    with db_mod.open_conn() as conn:
        status = conn.execute(
            "SELECT status FROM codes WHERE id=?", (code_id,)
        ).fetchone()[0]
        assert status == "adjudicated"


def test_adjudicate_with_raw_term_requires_canonicalization(client):
    """If a draft has operative_term_raw set but no term_id, adjudication
    must fail (DB CHECK), and the user must canonicalize first."""
    _login(client, "rs")
    client.post("/provisions/test-doc:1/codes", data={
        "operative_term_raw": "peaceful purposes",
        "operative_function": "permits",
        "source_span_ref": "First article body.",
    })
    with db_mod.open_conn() as conn:
        code_id = conn.execute(
            "SELECT id FROM codes WHERE provision_id='test-doc:1'"
        ).fetchone()[0]
    resp = client.post(f"/codes/{code_id}/adjudicate", follow_redirects=True)
    # CHECK surfaces as a flashed error; status stays human_draft.
    with db_mod.open_conn() as conn:
        status = conn.execute(
            "SELECT status FROM codes WHERE id=?", (code_id,)
        ).fetchone()[0]
        assert status == "human_draft"
    assert b"CHECK" in resp.data or b"could not" in resp.data or b"constraint" in resp.data


def test_update_adds_canonical_term_inline(client):
    """Editing a draft with a new canonical term should create the term and
    attach it, so adjudication then succeeds."""
    _login(client, "rs")
    client.post("/provisions/test-doc:1/codes", data={
        "operative_term_raw": "peaceful purposes",
        "operative_function": "permits",
        "source_span_ref": "First article body.",
    })
    with db_mod.open_conn() as conn:
        code_id = conn.execute(
            "SELECT id FROM codes WHERE provision_id='test-doc:1'"
        ).fetchone()[0]
    client.post(f"/codes/{code_id}", data={
        "operative_term_raw": "peaceful purposes",
        "term_new": "peaceful purposes",
        "operative_function": "permits",
        "source_span_ref": "First article body.",
    })
    with db_mod.open_conn() as conn:
        term = conn.execute(
            "SELECT term_id FROM codes WHERE id=?", (code_id,)
        ).fetchone()[0]
        assert term == "peaceful-purposes"  # slugified
        assert conn.execute(
            "SELECT canonical_label FROM terms WHERE id=?", (term,)
        ).fetchone()[0] == "peaceful purposes"
    # Now adjudication should succeed.
    client.post(f"/codes/{code_id}/adjudicate")
    with db_mod.open_conn() as conn:
        status = conn.execute(
            "SELECT status FROM codes WHERE id=?", (code_id,)
        ).fetchone()[0]
        assert status == "adjudicated"


def test_delete_draft(client):
    _login(client, "rs")
    client.post("/provisions/test-doc:1/codes", data={
        "operative_function": "permits",
        "source_span_ref": "x",
    })
    with db_mod.open_conn() as conn:
        code_id = conn.execute(
            "SELECT id FROM codes WHERE provision_id='test-doc:1'"
        ).fetchone()[0]
    client.post(f"/codes/{code_id}/delete")
    with db_mod.open_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM codes WHERE id=?", (code_id,)
        ).fetchone()[0]
        assert n == 0


def test_cannot_delete_adjudicated(client):
    _login(client, "rs")
    client.post("/provisions/test-doc:1/codes", data={
        "operative_function": "permits",
        "source_span_ref": "x",
    })
    with db_mod.open_conn() as conn:
        code_id = conn.execute(
            "SELECT id FROM codes WHERE provision_id='test-doc:1'"
        ).fetchone()[0]
    client.post(f"/codes/{code_id}/adjudicate")
    resp = client.post(f"/codes/{code_id}/delete")
    assert resp.status_code == 403
    with db_mod.open_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM codes WHERE id=?", (code_id,)).fetchone()[0]
        assert n == 1


def test_cannot_edit_someone_elses_draft(client):
    db_mod.add_coder("other", name="Other", role="secondary", is_human=True, model_ref=None)
    _login(client, "rs")
    client.post("/provisions/test-doc:1/codes", data={
        "operative_function": "permits",
        "source_span_ref": "x",
    })
    with db_mod.open_conn() as conn:
        code_id = conn.execute(
            "SELECT id FROM codes WHERE provision_id='test-doc:1'"
        ).fetchone()[0]
    _login(client, "other")
    resp = client.post(f"/codes/{code_id}", data={"source_span_ref": "y"})
    assert resp.status_code == 403


def test_accept_suggestion_creates_draft_and_preserves_suggestion(client):
    # Manually insert a suggestion (Phase 4 would do this).
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref, "
            "operative_function, suggested_confidence, rationale) "
            "VALUES('sug1', 'test-doc:1', 'llm', 'suggested', 'First article body.', "
            "'permits', 0.85, 'because it permits things')"
        )
        conn.commit()
    _login(client, "rs")
    client.post("/suggestions/sug1/accept")
    with db_mod.open_conn() as conn:
        rows = list(conn.execute(
            "SELECT status, coder_id, operative_function "
            "FROM codes WHERE provision_id='test-doc:1' ORDER BY status"
        ))
        statuses = {r["status"] for r in rows}
        assert statuses == {"suggested", "human_draft"}
        # Original suggestion is preserved with original coder.
        sug = next(r for r in rows if r["status"] == "suggested")
        assert sug["coder_id"] == "llm"
        # New draft is by the human, with same vocab.
        draft = next(r for r in rows if r["status"] == "human_draft")
        assert draft["coder_id"] == "rs"
        assert draft["operative_function"] == "permits"


def test_suggestion_disclaimer_visible_on_provision_page(client):
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
            "VALUES('sug1', 'test-doc:1', 'llm', 'suggested', 'x')"
        )
        conn.commit()
    resp = client.get("/provisions/test-doc:1")
    assert b"Unverified LLM suggestion" in resp.data


def test_set_coder_sets_cookie(client):
    resp = client.post("/set-coder", data={"coder": "rs", "next": "/"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "coder=rs" in resp.headers.get("Set-Cookie", "")
