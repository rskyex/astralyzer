from pathlib import Path

from astralyzer import codebook as cb

REPO = Path(__file__).resolve().parents[1]
CODEBOOK = REPO / "codebook" / "codebook.yaml"


def test_codebook_loads(tmp_db):
    n = cb.load_from_yaml(CODEBOOK)
    assert n >= 8
    fields = {f["field"] for f in cb.list_fields()}
    assert "operative_function" in fields
    assert "coupling_domains" in fields
    assert "operative_term_raw" in fields


def test_codebook_field_has_value_domain(tmp_db):
    cb.load_from_yaml(CODEBOOK)
    f = cb.get_field("operative_function")
    assert f is not None
    assert "permits" in f["value_domain"]
    assert "assigns_authority" in f["value_domain"]


def test_codebook_load_is_idempotent(tmp_db):
    cb.load_from_yaml(CODEBOOK)
    n_again = cb.load_from_yaml(CODEBOOK)
    assert n_again >= 8
    fields = cb.list_fields()
    # No duplicate rows: field is PK.
    assert len({f["field"] for f in fields}) == len(fields)
