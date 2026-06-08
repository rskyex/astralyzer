"""The review UI must hide the other reliability coder's codes while a
run is open, and reveal them after the run is computed."""
import uuid

import pytest

from astralyzer import db as db_mod
from astralyzer import ingest as ingest_mod
from astralyzer import reliability as rel
from astralyzer.review.app import create_app


META = dict(
    document_id="td",
    short_name="TD",
    full_title="Test",
    instrument_type="space",
    official_source_url="https://example.org",
    version_or_date="2026-01-01",
    retrieval_date="2026-05-24",
)
TEXT = "Article I\nBody one.\n\nArticle II\nBody two.\n"


def _insert_code(provision_id, coder_id, **fields):
    code_id = uuid.uuid4().hex[:16]
    cols = {"id": code_id, "provision_id": provision_id, "coder_id": coder_id,
            "status": "human_draft", "source_span_ref": "Body"}
    cols.update(fields)
    with db_mod.open_conn() as conn:
        names = ",".join(cols)
        placeholders = ",".join(f":{k}" for k in cols)
        conn.execute(f"INSERT INTO codes({names}) VALUES({placeholders})", cols)
        conn.commit()
    return code_id


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    ingest_mod.ingest_document(text=TEXT, **META)
    db_mod.add_coder("alice", name="A", role="lead", is_human=True, model_ref=None)
    db_mod.add_coder("bob",   name="B", role="secondary", is_human=True, model_ref=None)
    db_mod.add_coder("carol", name="C", role="other", is_human=True, model_ref=None)
    db_mod.add_coder("llm-c", name="Claude", role="llm",
                     is_human=False, model_ref="anthropic:claude-sonnet-4-6")
    return tmp_path


@pytest.fixture
def client(env):
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _as(client, handle):
    client.post("/set-coder", data={"coder": handle, "next": "/"})


def test_open_run_blinds_alice_from_bob(client):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1"])
    alice_code = _insert_code("td:1", "alice", operative_function="permits")
    bob_code = _insert_code("td:1", "bob", operative_function="restricts")
    _as(client, "alice")
    resp = client.get("/provisions/td:1")
    assert resp.status_code == 200
    assert alice_code.encode() in resp.data
    assert bob_code.encode() not in resp.data
    # Banner is visible.
    assert b"Reliability run active" in resp.data
    assert b"bob" in resp.data  # shown in banner


def test_open_run_blinds_bob_from_alice(client):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1"])
    alice_code = _insert_code("td:1", "alice", operative_function="permits")
    bob_code = _insert_code("td:1", "bob", operative_function="restricts")
    _as(client, "bob")
    resp = client.get("/provisions/td:1")
    assert bob_code.encode() in resp.data
    assert alice_code.encode() not in resp.data


def test_non_member_coder_sees_everything(client):
    """A coder outside the run sees both A's and B's codes (and no banner)."""
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1"])
    alice_code = _insert_code("td:1", "alice", operative_function="permits")
    bob_code = _insert_code("td:1", "bob", operative_function="restricts")
    _as(client, "carol")
    resp = client.get("/provisions/td:1")
    assert alice_code.encode() in resp.data
    assert bob_code.encode() in resp.data
    assert b"Reliability run active" not in resp.data


def test_llm_suggestions_remain_visible_during_open_run(client):
    """Per Phase 5 design notes: blinding hides ONLY the specific other
    reliability coder. LLM suggestions are visible (they aren't peer codes)."""
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1"])
    suggestion = _insert_code("td:1", "llm-c", status="suggested",
                              operative_function="permits")
    _as(client, "alice")
    resp = client.get("/provisions/td:1")
    assert suggestion.encode() in resp.data


def test_blinding_lifts_after_compute(client):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1"])
    _insert_code("td:1", "alice", operative_function="permits")
    bob_code = _insert_code("td:1", "bob", operative_function="restricts")
    rel.compute_run("r1")
    _as(client, "alice")
    resp = client.get("/provisions/td:1")
    assert bob_code.encode() in resp.data
    assert b"Reliability run active" not in resp.data


def test_provision_outside_run_not_blinded(client):
    """A run on td:1 does not blind alice on td:2."""
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1"])
    bob_code = _insert_code("td:2", "bob", operative_function="permits")
    _as(client, "alice")
    resp = client.get("/provisions/td:2")
    assert bob_code.encode() in resp.data
    assert b"Reliability run active" not in resp.data


def test_reliability_show_page_shows_kappa_after_compute(client):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1", "td:2"])
    for pid in ["td:1", "td:2"]:
        _insert_code(pid, "alice", operative_function="permits")
        _insert_code(pid, "bob", operative_function="permits")
    rel.compute_run("r1")
    resp = client.get("/reliability/r1")
    assert resp.status_code == 200
    assert b"operative_function" in resp.data
    assert b"1.000" in resp.data  # perfect agreement


def test_reliability_index_lists_runs(client):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["td:1"])
    resp = client.get("/reliability")
    assert resp.status_code == 200
    assert b"r1" in resp.data
    assert b"alice" in resp.data
    assert b"bob" in resp.data
