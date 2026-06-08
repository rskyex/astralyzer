"""Export tests: file contents, manifest provenance, version write-once."""
import json
import uuid

import pytest

from astralyzer import db as db_mod
from astralyzer import export as export_mod
from astralyzer import ingest as ingest_mod


META = dict(
    document_id="ost",
    short_name="OST",
    full_title="Outer Space Treaty",
    instrument_type="space",
    official_source_url="https://example.org",
    version_or_date="1967-01-27",
    retrieval_date="2026-05-24",
)


def _adjudicate(provision_id, coder_id, **fields):
    cid = uuid.uuid4().hex[:16]
    cols = {"id": cid, "provision_id": provision_id, "coder_id": coder_id,
            "status": "adjudicated", "source_span_ref": "x"}
    cols.update(fields)
    domains = cols.pop("coupling_domains", None)
    with db_mod.open_conn() as conn:
        names = ",".join(cols)
        placeholders = ",".join(f":{k}" for k in cols)
        conn.execute(f"INSERT INTO codes({names}) VALUES({placeholders})", cols)
        for d in domains or []:
            conn.execute(
                "INSERT INTO code_coupling_domains(code_id, domain) VALUES(?, ?)",
                (cid, d),
            )
        conn.commit()
    return cid


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    ingest_mod.ingest_document(
        text="Article I\nFirst body.\n\nArticle II\nSecond body.\n", **META,
    )
    db_mod.add_coder("rs", name="R", role="lead", is_human=True, model_ref=None)
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO terms(id, canonical_label, created_by) "
            "VALUES ('peaceful-purposes', 'peaceful purposes', 'rs')"
        )
        conn.commit()
    _adjudicate("ost:1", "rs",
                operative_term_raw="peaceful purposes",
                term_id="peaceful-purposes",
                operative_function="permits",
                authority_default="operating_party",
                verification_mechanism="none",
                independent_epistemic_access=0,
                ai_operation_effect="not addressed",
                rationale="Article I uses broad language",
                coupling_domains=["space", "ai"])
    _adjudicate("ost:2", "rs",
                operative_function="restricts",
                authority_default="launching_state",
                coupling_domains=["space"])
    return tmp_path


def test_release_writes_all_files(env):
    out = env / "exports" / "0.1.0"
    result = export_mod.export_release("0.1.0", out_dir=out, notes="first")
    assert result["version"] == "0.1.0"
    for name in ("codes.csv", "codes.json", "documents.csv", "provisions.csv",
                 "terms.csv", "codebook.json", "manifest.json"):
        assert (out / name).exists(), f"missing: {name}"
    # Analysis subdir.
    assert (out / "analysis" / "iea_by_instrument.csv").exists()


def test_codes_csv_includes_provenance(env):
    out = env / "exports" / "0.1.0"
    export_mod.export_release("0.1.0", out_dir=out)
    csv = (out / "codes.csv").read_text()
    # Every adjudicated row carries document + provision + coder.
    assert "ost" in csv and "OST" in csv
    assert "ost:1" in csv and "Article I" in csv
    assert ",rs," in csv  # coder
    # coupling_domains pipe-delimited.
    assert "space|ai" in csv or "ai|space" in csv


def test_codes_json_preserves_coupling_domains_as_list(env):
    out = env / "exports" / "0.1.0"
    export_mod.export_release("0.1.0", out_dir=out)
    rows = json.loads((out / "codes.json").read_text())
    assert len(rows) == 2
    ost1 = next(r for r in rows if r["provision_id"] == "ost:1")
    assert isinstance(ost1["coupling_domains"], list)
    assert set(ost1["coupling_domains"]) == {"space", "ai"}


def test_manifest_contains_sha256_and_git_sha(env):
    out = env / "exports" / "0.1.0"
    export_mod.export_release("0.1.0", out_dir=out)
    m = json.loads((out / "manifest.json").read_text())
    assert m["version"] == "0.1.0"
    assert m["is_release"] is True
    assert m["generated_at"].endswith("Z")
    assert m["counts"]["adjudicated_codes"] == 2
    assert m["counts"]["documents"] == 1
    docs = {d["id"]: d for d in m["documents"]}
    assert "ost" in docs
    assert len(docs["ost"]["raw_text_sha256"]) == 64
    # git_commit_sha is optional (None if not in a git repo); type only.
    assert m["git_commit_sha"] is None or isinstance(m["git_commit_sha"], str)


def test_release_registers_in_dataset_versions(env):
    out = env / "exports" / "0.1.0"
    export_mod.export_release("0.1.0", out_dir=out, notes="first")
    rows = export_mod.list_versions()
    assert len(rows) == 1
    assert rows[0]["version"] == "0.1.0"
    assert rows[0]["n_adjudicated_codes"] == 2
    assert rows[0]["notes"] == "first"


def test_release_version_is_write_once(env):
    out1 = env / "exports" / "0.1.0"
    export_mod.export_release("0.1.0", out_dir=out1)
    out2 = env / "exports" / "0.1.0-take2"
    with pytest.raises(export_mod.ExportError, match="already registered"):
        export_mod.export_release("0.1.0", out_dir=out2)


def test_release_refuses_nonempty_existing_dir(env):
    out = env / "exports" / "0.1.0"
    out.mkdir(parents=True)
    (out / "junk").write_text("x")
    with pytest.raises(export_mod.ExportError, match="non-empty"):
        export_mod.export_release("0.1.0", out_dir=out)


def test_wip_writes_files_but_does_not_register(env):
    result = export_mod.export_wip()
    target = result["target"]
    assert target.exists()
    assert (target / "codes.csv").exists()
    m = json.loads((target / "manifest.json").read_text())
    assert m["is_release"] is False
    assert m["version"] is None
    assert m["generated_at"].endswith("Z")
    # No row registered.
    assert export_mod.list_versions() == []


def test_wip_filename_includes_timestamp_and_sha(env):
    result = export_mod.export_wip()
    name = result["target"].name
    assert name.startswith("wip-")
    # wip-<utc-stamp>-<git-or-nogit>
    parts = name.split("-")
    assert len(parts) >= 3


def test_codebook_exported_as_json(env):
    out = env / "exports" / "0.1.0"
    db_mod.add_coder("dummy", name="d", role="x", is_human=True, model_ref=None)
    # Seed a codebook row so the export has something to write.
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO codebook_fields(field, definition, decision_rules, "
            "                            value_domain, version) "
            "VALUES ('operative_function', 'role', 'rules', '[\"permits\"]', '0.1')"
        )
        conn.commit()
    export_mod.export_release("0.1.0", out_dir=out)
    cb = json.loads((out / "codebook.json").read_text())
    fields = {f["field"]: f for f in cb}
    assert "operative_function" in fields
    assert fields["operative_function"]["value_domain"] == ["permits"]


def test_export_with_no_figures_flag(env):
    out = env / "exports" / "0.1.0"
    export_mod.export_release("0.1.0", out_dir=out, with_figures=False)
    assert (out / "analysis" / "iea_by_instrument.csv").exists()
    assert not (out / "analysis" / "iea_by_instrument.pdf").exists()
