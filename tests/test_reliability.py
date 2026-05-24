"""Unit tests for the reliability module: kappa math, sample selection,
run creation guards, compute path."""
import math
import sqlite3
import uuid

import pytest

from astralyzer import db as db_mod
from astralyzer import ingest as ingest_mod
from astralyzer import reliability as rel


META = dict(
    document_id="ost-test",
    short_name="OST-Test",
    full_title="Test treaty",
    instrument_type="space",
    official_source_url="https://example.org",
    version_or_date="1967-01-27",
    retrieval_date="2026-05-24",
)

# 5 segmentable articles.
TEXT = "\n\n".join(
    f"Article {n}\nProvision body number {n}." for n in
    ["I", "II", "III", "IV", "V"]
)


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    ingest_mod.ingest_document(text=TEXT, **META)
    db_mod.add_coder("alice", name="Alice", role="lead", is_human=True, model_ref=None)
    db_mod.add_coder("bob",   name="Bob",   role="secondary", is_human=True, model_ref=None)
    db_mod.add_coder("llm-c", name="Claude", role="llm",
                     is_human=False, model_ref="anthropic:claude-sonnet-4-6")
    return tmp_path


# ---------- pure math --------------------------------------------------

def test_cohens_kappa_perfect_agreement():
    assert rel.cohens_kappa([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0


def test_cohens_kappa_chance_only_is_zero():
    # Both annotators choose between two categories with 50/50 split, agreement
    # only by chance → kappa around 0.
    a = ["x", "y", "x", "y"]
    b = ["y", "x", "x", "y"]
    k = rel.cohens_kappa(a, b)
    assert -0.5 < k < 0.5


def test_cohens_kappa_treats_none_as_category():
    # Both said None → that's still agreement.
    assert rel.cohens_kappa([None, None, None], [None, None, None]) == 1.0


def test_cohens_kappa_empty_returns_none():
    assert rel.cohens_kappa([], []) is None


def test_cohens_kappa_single_category_returns_one_or_none():
    # Both annotators always pick the same single value → po=1, pe=1, undefined.
    # Our impl returns 1.0 when po >= 1.
    assert rel.cohens_kappa(["x", "x", "x"], ["x", "x", "x"]) == 1.0


def test_percent_agreement():
    assert rel.percent_agreement([1, 1, 0], [1, 1, 0]) == 1.0
    assert rel.percent_agreement([1, 1, 0], [1, 0, 0]) == pytest.approx(2 / 3)
    assert rel.percent_agreement([], []) is None


# ---------- sample selection ------------------------------------------

def test_select_sample_returns_all_when_n_exceeds_pool(env):
    sample = rel.select_sample(document_id="ost-test", n=100)
    assert len(sample) == 5  # all provisions


def test_select_sample_deterministic_with_seed(env):
    a = rel.select_sample(document_id="ost-test", n=3, seed=42)
    b = rel.select_sample(document_id="ost-test", n=3, seed=42)
    assert a == b
    assert len(set(a)) == 3  # no duplicates


def test_select_sample_empty_pool_errors(tmp_db):
    with pytest.raises(rel.ReliabilityError, match="no provisions"):
        rel.select_sample(n=5)


# ---------- run creation guards ---------------------------------------

def test_create_run_rejects_same_coder(env):
    with pytest.raises(rel.ReliabilityError, match="distinct"):
        rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="alice",
                       sample_provisions=["ost-test:1"])


def test_create_run_rejects_llm_coder_via_trigger(env):
    """The DB trigger enforces both coders are human."""
    with pytest.raises(sqlite3.IntegrityError, match="human coders"):
        rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="llm-c",
                       sample_provisions=["ost-test:1"])


def test_create_run_rejects_unknown_provision(env):
    with pytest.raises(rel.ReliabilityError, match="no such provision"):
        rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                       sample_provisions=["ost-test:1", "nope:99"])


def test_create_run_writes_membership_join_table(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1", "ost-test:2"])
    with db_mod.open_conn() as conn:
        pids = sorted(r["provision_id"] for r in conn.execute(
            "SELECT provision_id FROM reliability_run_provisions WHERE run_id='r1'"))
        assert pids == ["ost-test:1", "ost-test:2"]


def test_create_run_status_is_open(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1"])
    run = rel.get_run("r1")
    assert run["status"] == "open"


# ---------- blinding helpers ------------------------------------------

def test_coders_blinded_from_returns_other_for_member_only(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1"])
    assert rel.coders_blinded_from("ost-test:1", "alice") == ["bob"]
    assert rel.coders_blinded_from("ost-test:1", "bob") == ["alice"]
    # Non-member sees no blinding.
    db_mod.add_coder("carol", name="C", role="other", is_human=True, model_ref=None)
    assert rel.coders_blinded_from("ost-test:1", "carol") == []
    # Provision not in the run.
    assert rel.coders_blinded_from("ost-test:5", "alice") == []


def test_blinding_lifts_when_status_becomes_computed(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1"])
    assert rel.coders_blinded_from("ost-test:1", "alice") == ["bob"]
    _insert_code(provision_id="ost-test:1", coder_id="alice",
                 operative_function="permits", source_span_ref="x")
    _insert_code(provision_id="ost-test:1", coder_id="bob",
                 operative_function="permits", source_span_ref="x")
    rel.compute_run("r1")
    assert rel.coders_blinded_from("ost-test:1", "alice") == []


# ---------- compute ---------------------------------------------------

def _insert_code(**fields):
    """Insert a human_draft code. fields are passed straight to the codes row."""
    code_id = uuid.uuid4().hex[:16]
    cols = {"id": code_id, "status": "human_draft"}
    cols.update(fields)
    domains = cols.pop("coupling_domains", None)
    with db_mod.open_conn() as conn:
        placeholders = ",".join(f":{k}" for k in cols)
        names = ",".join(cols.keys())
        conn.execute(f"INSERT INTO codes({names}) VALUES ({placeholders})", cols)
        for d in domains or []:
            conn.execute(
                "INSERT INTO code_coupling_domains(code_id, domain) VALUES(?, ?)",
                (code_id, d),
            )
        conn.commit()
    return code_id


def test_compute_kappa_with_perfect_agreement(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1", "ost-test:2", "ost-test:3"])
    for pid in ["ost-test:1", "ost-test:2", "ost-test:3"]:
        for coder in ("alice", "bob"):
            _insert_code(provision_id=pid, coder_id=coder,
                         operative_function="permits",
                         authority_default="operating_party",
                         coupling_domains=["space"],
                         source_span_ref="x")
    result = rel.compute_run("r1")
    assert result.n_pairs == 3
    assert result.n_missing == 0
    assert result.kappa["operative_function"] == 1.0
    assert result.kappa["coupling_domain:space"] == 1.0
    assert result.agreement["operative_function"] == 1.0
    # Run is now computed; row state reflects this.
    assert rel.get_run("r1")["status"] == "computed"


def test_compute_kappa_with_total_disagreement(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1", "ost-test:2",
                                      "ost-test:3", "ost-test:4"])
    pairs = [("permits", "restricts"), ("restricts", "permits"),
             ("permits", "restricts"), ("restricts", "permits")]
    for pid, (a, b) in zip(["ost-test:1", "ost-test:2", "ost-test:3", "ost-test:4"], pairs):
        _insert_code(provision_id=pid, coder_id="alice",
                     operative_function=a, source_span_ref="x")
        _insert_code(provision_id=pid, coder_id="bob",
                     operative_function=b, source_span_ref="x")
    result = rel.compute_run("r1")
    # Total disagreement on a 50/50 split → kappa around -1.0.
    assert result.kappa["operative_function"] is not None
    assert result.kappa["operative_function"] < -0.5


def test_compute_skips_provisions_with_missing_codes(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1", "ost-test:2"])
    # Only alice codes ost-test:1; both code ost-test:2.
    _insert_code(provision_id="ost-test:1", coder_id="alice",
                 operative_function="permits", source_span_ref="x")
    _insert_code(provision_id="ost-test:2", coder_id="alice",
                 operative_function="permits", source_span_ref="x")
    _insert_code(provision_id="ost-test:2", coder_id="bob",
                 operative_function="permits", source_span_ref="x")
    result = rel.compute_run("r1")
    assert result.n_pairs == 1
    assert result.n_missing == 1


def test_compute_uses_latest_human_code_per_coder(env):
    """If a coder has multiple drafts, the most recent is used."""
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1"])
    # alice's older draft.
    _insert_code(provision_id="ost-test:1", coder_id="alice",
                 operative_function="restricts", source_span_ref="x")
    # alice's newer draft (later created_at by sqlite default ordering).
    import time
    time.sleep(1.05)
    _insert_code(provision_id="ost-test:1", coder_id="alice",
                 operative_function="permits", source_span_ref="x")
    _insert_code(provision_id="ost-test:1", coder_id="bob",
                 operative_function="permits", source_span_ref="x")
    result = rel.compute_run("r1")
    assert result.kappa["operative_function"] == 1.0  # latest matched


def test_disagreements_lists_per_field_diffs(env):
    rel.create_run(run_id="r1", coder_a_id="alice", coder_b_id="bob",
                   sample_provisions=["ost-test:1"])
    _insert_code(provision_id="ost-test:1", coder_id="alice",
                 operative_function="permits",
                 authority_default="operating_party",
                 coupling_domains=["space"],
                 source_span_ref="x")
    _insert_code(provision_id="ost-test:1", coder_id="bob",
                 operative_function="restricts",
                 authority_default="operating_party",  # match
                 coupling_domains=["space", "ai"],
                 source_span_ref="x")
    rel.compute_run("r1")
    diffs = rel.list_disagreements("r1")
    assert len(diffs) == 1
    fields = {d["field"] for d in diffs[0]["diffs"]}
    assert "operative_function" in fields
    assert "coupling_domains" in fields
    assert "authority_default" not in fields  # matched
