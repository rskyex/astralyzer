from astralyzer import db


def test_migrations_apply_clean(tmp_db):
    versions = [v for v, _ in db.applied_migrations()]
    assert "0001_initial_schema" in versions


def test_migrate_is_idempotent(tmp_db):
    assert db.migrate() == []


def test_core_tables_exist(tmp_db):
    expected = {
        "documents", "provisions", "coders", "terms", "codes",
        "code_coupling_domains", "term_migrations", "codebook_fields",
        "reliability_runs", "dataset_versions", "schema_migrations",
    }
    with db.open_conn() as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = expected - names
    assert not missing, f"missing tables: {missing}"


def test_foreign_keys_are_on(tmp_db):
    with db.open_conn() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
