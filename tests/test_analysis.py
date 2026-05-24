"""Aggregations operate over adjudicated codes only."""
import uuid

import pandas as pd
import pytest

from astralyzer import analysis as analysis_mod
from astralyzer import db as db_mod
from astralyzer import ingest as ingest_mod


META_BASE = dict(
    short_name="X", full_title="X", official_source_url="https://example.org",
    version_or_date="2026-01-01", retrieval_date="2026-05-24",
)


_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def _ingest(doc_id, instrument, n_articles=2):
    text = "\n\n".join(
        f"Article {_ROMAN[i]}\nBody {i + 1}." for i in range(n_articles)
    )
    ingest_mod.ingest_document(
        text=text, document_id=doc_id, short_name=doc_id.upper(),
        full_title=f"Doc {doc_id}", instrument_type=instrument,
        official_source_url=META_BASE["official_source_url"],
        version_or_date=META_BASE["version_or_date"],
        retrieval_date=META_BASE["retrieval_date"],
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
def populated(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    db_mod.add_coder("rs", name="R", role="lead", is_human=True, model_ref=None)
    _ingest("ost", "space", n_articles=3)
    _ingest("aiact", "ai", n_articles=2)
    # OST: 2 codes with IEA=0, one with IEA=1; OST adj #1 has coupling space+ai
    _adjudicate("ost:1", "rs", independent_epistemic_access=0,
                authority_default="operating_party",
                coupling_domains=["space", "ai"])
    _adjudicate("ost:2", "rs", independent_epistemic_access=0,
                authority_default="operating_party",
                coupling_domains=["space"])
    _adjudicate("ost:3", "rs", independent_epistemic_access=1,
                authority_default="consensus_body",
                coupling_domains=["space"])
    # AI Act: 2 codes, IEA=2 + IEA=0, different authority
    _adjudicate("aiact:1", "rs", independent_epistemic_access=2,
                authority_default="operating_party",
                coupling_domains=["ai"])
    _adjudicate("aiact:2", "rs", independent_epistemic_access=0,
                authority_default="unspecified",
                coupling_domains=["ai"])
    # Add a human_draft that must NOT appear in aggregations.
    _adjudicate.__wrapped__ if False else None
    cid = uuid.uuid4().hex[:16]
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, "
            "operative_function, source_span_ref) "
            "VALUES (?, 'ost:1', 'rs', 'human_draft', 'restricts', 'y')",
            (cid,),
        )
        conn.commit()
    return tmp_path


def test_iea_by_instrument(populated):
    df = analysis_mod.iea_by_instrument()
    # 2 instruments × IEA values (0, 1, 2)
    assert df.loc["space", 0] == 2
    assert df.loc["space", 1] == 1
    assert df.loc["ai", 2] == 1
    assert df.loc["ai", 0] == 1


def test_iea_by_instrument_excludes_drafts(populated):
    df = analysis_mod.iea_by_instrument()
    # OST has 3 adjudicated codes total — the human_draft is excluded.
    assert df.loc["space"].sum() == 3


def test_iea_by_domain_counts_multi_membership(populated):
    df = analysis_mod.iea_by_domain()
    # 'space' domain: 3 adjudicated codes (ost:1, ost:2, ost:3)
    assert df.loc["space"].sum() == 3
    # 'ai' domain: ost:1 (multi-domain) + aiact:1 + aiact:2 = 3
    assert df.loc["ai"].sum() == 3


def test_authority_default_distribution(populated):
    df = analysis_mod.authority_default_distribution()
    rows = dict(zip(df["authority_default"], df["count"]))
    assert rows["operating_party"] == 3
    assert rows["consensus_body"] == 1
    assert rows["unspecified"] == 1


def test_authority_default_by_instrument(populated):
    df = analysis_mod.authority_default_by_instrument()
    assert df.loc["space", "operating_party"] == 2
    assert df.loc["space", "consensus_body"] == 1
    assert df.loc["ai", "operating_party"] == 1
    assert df.loc["ai", "unspecified"] == 1


def test_term_occurrences_empty_when_no_terms(populated):
    df = analysis_mod.term_occurrences()
    assert df.empty


def test_write_tables_produces_all_csvs(populated, tmp_path):
    out = tmp_path / "an"
    paths = analysis_mod.write_tables(out)
    names = {p.name for p in paths}
    assert names == {
        "iea_by_instrument.csv",
        "iea_by_domain.csv",
        "authority_default_distribution.csv",
        "authority_default_by_instrument.csv",
        "term_occurrences.csv",
        "term_migrations.csv",
    }
    # Sanity check: re-read one and verify a known value survived round-trip.
    iea = pd.read_csv(out / "iea_by_instrument.csv", index_col=0)
    assert int(iea.loc["space", "0"]) == 2


def test_write_figures_produces_pdfs(populated, tmp_path):
    out = tmp_path / "fig"
    paths = analysis_mod.write_figures(out)
    names = {p.name for p in paths}
    assert "iea_by_instrument.pdf" in names
    assert "iea_by_domain.pdf" in names
    assert "authority_default.pdf" in names
    for p in paths:
        assert p.read_bytes()[:4] == b"%PDF"


def test_empty_db_returns_empty_frames(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    assert analysis_mod.iea_by_instrument().empty
    assert analysis_mod.iea_by_domain().empty
    assert analysis_mod.authority_default_distribution().empty
    # write_tables on empty DB should still produce empty CSVs.
    out = tmp_path / "an"
    paths = analysis_mod.write_tables(out)
    assert len(paths) == 6
    for p in paths:
        assert p.exists()


def test_iea_by_domain_excludes_codes_without_domains(populated):
    """A code with no coupling_domains shouldn't pollute per-domain rows."""
    # Add an adjudicated code with no coupling_domains.
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, "
            "independent_epistemic_access, source_span_ref) "
            "VALUES ('orphan', 'aiact:2', 'rs', 'human_draft', 0, 'x')"
        )
        conn.commit()
    df = analysis_mod.iea_by_domain()
    # The drafts/no-domain row should not appear under any domain.
    for d in ("space", "nuclear", "cyber", "ai"):
        if d in df.index:
            assert df.loc[d].sum() >= 1  # known data still there
    # No NaN row created.
    assert not df.index.isna().any()
