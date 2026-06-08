import pytest

from astralyzer import db as db_mod
from astralyzer import ingest as ingest_mod
from astralyzer.db import open_conn

DOC_TEXT = (
    "Preamble paragraph.\n\n"
    "Article I\n"
    "The first article.\n\n"
    "Article II\n"
    "The second article.\n\n"
    "Article III\n"
    "The third article.\n"
)

META = dict(
    document_id="ost-test",
    short_name="OST-Test",
    full_title="Outer Space Treaty (test fixture)",
    instrument_type="space",
    official_source_url="https://example.org/ost",
    version_or_date="1967-01-27",
    retrieval_date="2026-05-24",
)


@pytest.fixture
def ingest_env(tmp_db, tmp_path, monkeypatch):
    """tmp_db plus a redirected ROOT so raw text writes land under tmp_path."""
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    return tmp_path


def test_ingest_creates_document_and_provisions(ingest_env):
    """Default segmenter: preamble (pre-marker prose) + the three Article markers."""
    result = ingest_mod.ingest_document(text=DOC_TEXT, **META)
    assert result.status == "created"
    assert result.n_provisions == 4

    with open_conn() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (META["document_id"],)
        ).fetchone()
        assert doc["raw_text_sha256"] == result.sha256

        prov = list(conn.execute(
            "SELECT * FROM provisions WHERE document_id = ? ORDER BY ordinal",
            (META["document_id"],)
        ))
        assert [p["citation_anchor"] for p in prov] == [
            "Preamble", "Article I", "Article II", "Article III"
        ]
        assert prov[0]["id"] == "ost-test:1"
        assert prov[1]["id"] == "ost-test:2"


def test_ingest_is_idempotent_on_identical_content(ingest_env):
    first = ingest_mod.ingest_document(text=DOC_TEXT, **META)
    assert first.status == "created"
    second = ingest_mod.ingest_document(text=DOC_TEXT, **META)
    assert second.status == "unchanged"
    assert second.sha256 == first.sha256
    assert second.n_provisions == first.n_provisions


def test_ingest_refuses_sha_mismatch(ingest_env):
    ingest_mod.ingest_document(text=DOC_TEXT, **META)
    with pytest.raises(ingest_mod.IngestError, match="different sha256"):
        ingest_mod.ingest_document(text=DOC_TEXT + "\n\nArticle IV\nNew.\n", **META)


def test_ingest_writes_raw_text_file(ingest_env):
    ingest_mod.ingest_document(text=DOC_TEXT, **META)
    raw_path = ingest_env / "data" / "raw" / "ost-test.txt"
    assert raw_path.exists()
    assert raw_path.read_text(encoding="utf-8") == DOC_TEXT


def test_ingest_single_provision_mode(ingest_env):
    result = ingest_mod.ingest_document(text=DOC_TEXT, single_provision=True, **META)
    assert result.n_provisions == 1
    info = ingest_mod.show_document(META["document_id"])
    assert info["provisions"][0]["citation_anchor"] == "(whole)"
    assert info["provisions"][0]["char_start"] == 0
    assert info["provisions"][0]["char_end"] == len(DOC_TEXT)


def test_ingest_with_manual_segments_override(ingest_env):
    """Manual override beats the default, including capturing the preamble."""
    overrides = [
        {"anchor": "Preamble", "char_start": 0},
        {"anchor": "Art. I",   "char_start": DOC_TEXT.index("Article I")},
        {"anchor": "Art. II",  "char_start": DOC_TEXT.index("Article II")},
        {"anchor": "Art. III", "char_start": DOC_TEXT.index("Article III")},
    ]
    result = ingest_mod.ingest_document(
        text=DOC_TEXT, segments_override=overrides, **META,
    )
    assert result.n_provisions == 4
    info = ingest_mod.show_document(META["document_id"])
    assert [p["citation_anchor"] for p in info["provisions"]] == [
        "Preamble", "Art. I", "Art. II", "Art. III"
    ]
    assert info["provisions"][0]["text"].startswith("Preamble paragraph.")


def test_ingest_rejects_both_override_and_single_provision(ingest_env):
    with pytest.raises(ingest_mod.IngestError):
        ingest_mod.ingest_document(
            text=DOC_TEXT,
            segments_override=[{"anchor": "a", "char_start": 0}],
            single_provision=True,
            **META,
        )


def test_load_segments_yaml_parses_file(ingest_env):
    yaml_path = ingest_env / "segs.yaml"
    yaml_path.write_text(
        "segments:\n"
        "  - anchor: Preamble\n"
        "    char_start: 0\n"
        "  - anchor: Art. I\n"
        f"    char_start: {DOC_TEXT.index('Article I')}\n",
        encoding="utf-8",
    )
    overrides = ingest_mod.load_segments_yaml(yaml_path)
    assert overrides[0]["anchor"] == "Preamble"
    assert overrides[1]["char_start"] == DOC_TEXT.index("Article I")


def test_load_segments_yaml_rejects_missing_keys(ingest_env):
    yaml_path = ingest_env / "bad.yaml"
    yaml_path.write_text("segments:\n  - anchor: a\n", encoding="utf-8")
    with pytest.raises(ingest_mod.IngestError):
        ingest_mod.load_segments_yaml(yaml_path)


def test_ingest_propagates_invalid_instrument_type(ingest_env):
    import sqlite3
    bad = dict(META)
    bad["instrument_type"] = "made-up"
    with pytest.raises(sqlite3.IntegrityError):
        ingest_mod.ingest_document(text=DOC_TEXT, **bad)


def test_show_document_returns_none_for_missing(ingest_env):
    assert ingest_mod.show_document("does-not-exist") is None


def test_list_documents_orders_by_id(ingest_env):
    ingest_mod.ingest_document(
        text="Article I\nA.\n",
        **{**META, "document_id": "zzz", "short_name": "Z"},
    )
    ingest_mod.ingest_document(
        text="Article I\nB.\n",
        **{**META, "document_id": "aaa", "short_name": "A"},
    )
    rows = ingest_mod.list_documents()
    assert [r["id"] for r in rows] == ["aaa", "zzz"]
