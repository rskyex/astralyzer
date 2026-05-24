import pytest

from astralyzer import db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh migrated DB at a tmp path, isolated via ASTRALYZER_DB_PATH."""
    p = tmp_path / "astralyzer.db"
    monkeypatch.setenv("ASTRALYZER_DB_PATH", str(p))
    db.migrate()
    return p
