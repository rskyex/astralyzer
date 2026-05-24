"""Runner integration tests using the deterministic MockProvider + a
hand-written ScriptedProvider for edge cases (invalid spans, exceptions)."""
from dataclasses import dataclass

import pytest

from astralyzer import db as db_mod
from astralyzer import ingest as ingest_mod
from astralyzer.suggest.provider import SuggestionPayload
from astralyzer.suggest.runner import RunOutcome, SuggestionError, run_suggestions


META = dict(
    document_id="ost-test",
    short_name="OST-Test",
    full_title="Test treaty",
    instrument_type="space",
    official_source_url="https://example.org",
    version_or_date="1967-01-27",
    retrieval_date="2026-05-24",
)

TEXT = (
    "Article I\n"
    "The exploration and use of outer space shall be carried out for the "
    "benefit and in the interests of all countries.\n\n"
    "Article II\n"
    "Outer space is not subject to national appropriation.\n"
)


@pytest.fixture
def env(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    ingest_mod.ingest_document(text=TEXT, **META)
    db_mod.add_coder("rs", name="R. Sky", role="lead", is_human=True, model_ref=None)
    db_mod.add_coder("llm-c", name="Claude", role="llm",
                     is_human=False, model_ref="anthropic:claude-sonnet-4-6")
    # Seed codebook directly so the runner can load it.
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO codebook_fields(field, definition, decision_rules, version) "
            "VALUES ('operative_function', 'role', 'rules', '0.1')"
        )
        conn.commit()
    return tmp_path


@dataclass
class ScriptedProvider:
    """Provider that returns a hand-written payload, for edge-case tests."""
    model_ref: str
    payload: SuggestionPayload | Exception

    def suggest(self, *, document, provision, codebook):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_run_rejects_human_coder(env):
    from astralyzer.suggest.mock import MockProvider
    with pytest.raises(SuggestionError, match="is_human=1"):
        run_suggestions(provider=MockProvider(), coder_id="rs",
                        provision_id="ost-test:1")


def test_run_rejects_unknown_coder(env):
    from astralyzer.suggest.mock import MockProvider
    with pytest.raises(SuggestionError, match="no such coder"):
        run_suggestions(provider=MockProvider(), coder_id="ghost",
                        provision_id="ost-test:1")


def test_run_requires_a_target_selector(env):
    from astralyzer.suggest.mock import MockProvider
    with pytest.raises(SuggestionError, match="--provision"):
        run_suggestions(provider=MockProvider(), coder_id="llm-c")


def test_run_on_single_provision_creates_suggestion(env):
    from astralyzer.suggest.mock import MockProvider
    outcomes = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                               provision_id="ost-test:1")
    assert len(outcomes) == 1
    assert outcomes[0].status == "suggested"
    assert outcomes[0].code_id is not None
    with db_mod.open_conn() as conn:
        row = conn.execute(
            "SELECT status, coder_id, suggested_confidence, rationale "
            "FROM codes WHERE provision_id='ost-test:1'"
        ).fetchone()
        assert row["status"] == "suggested"
        assert row["coder_id"] == "llm-c"
        assert row["suggested_confidence"] == 0.5
        # Rationale carries the model ref as audit.
        assert "[model: mock:v1]" in row["rationale"]


def test_run_is_idempotent_skips_existing(env):
    from astralyzer.suggest.mock import MockProvider
    first = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                            provision_id="ost-test:1")
    assert first[0].status == "suggested"
    second = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                             provision_id="ost-test:1")
    assert second[0].status == "skipped"
    with db_mod.open_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM codes WHERE provision_id='ost-test:1' AND coder_id='llm-c'"
        ).fetchone()[0]
        assert n == 1


def test_regenerate_replaces_existing(env):
    from astralyzer.suggest.mock import MockProvider
    run_suggestions(provider=MockProvider(), coder_id="llm-c",
                    provision_id="ost-test:1")
    redo = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                           provision_id="ost-test:1", regenerate=True)
    assert redo[0].status == "suggested"
    with db_mod.open_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM codes WHERE provision_id='ost-test:1' AND coder_id='llm-c'"
        ).fetchone()[0]
        assert n == 1  # one replacement, not two side-by-side


def test_run_on_document_processes_all_provisions(env):
    from astralyzer.suggest.mock import MockProvider
    outcomes = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                               document_id="ost-test")
    assert len(outcomes) == 2
    assert all(o.status == "suggested" for o in outcomes)


def test_limit_caps_processing(env):
    from astralyzer.suggest.mock import MockProvider
    outcomes = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                               all_=True, limit=1)
    assert sum(1 for o in outcomes if o.status == "suggested") == 1


def test_dry_run_does_not_write(env):
    from astralyzer.suggest.mock import MockProvider
    outcomes = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                               provision_id="ost-test:1", dry_run=True)
    assert outcomes[0].status == "dry-run"
    with db_mod.open_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
        assert n == 0


def test_invalid_span_is_rejected_without_writing(env):
    bad = SuggestionPayload(
        source_span_ref="this exact phrase does not appear anywhere in the provision",
        rationale="ignore me", confidence=0.5,
    )
    outcomes = run_suggestions(
        provider=ScriptedProvider(model_ref="scripted", payload=bad),
        coder_id="llm-c", provision_id="ost-test:1",
    )
    assert outcomes[0].status == "failed"
    assert "verbatim substring" in outcomes[0].detail
    with db_mod.open_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
        assert n == 0


def test_span_validation_tolerates_whitespace(env):
    # Original: "Article I\nThe exploration and use of outer space ..."
    # Span with collapsed whitespace should still be accepted as a verbatim
    # match modulo whitespace.
    spaced = "Article I The exploration and use of outer space"
    payload = SuggestionPayload(
        source_span_ref=spaced, rationale="ok", confidence=0.5,
        coupling_domains=["space"],
    )
    outcomes = run_suggestions(
        provider=ScriptedProvider(model_ref="scripted", payload=payload),
        coder_id="llm-c", provision_id="ost-test:1",
    )
    assert outcomes[0].status == "suggested", outcomes[0].detail


def test_provider_exception_is_caught_and_reported(env):
    outcomes = run_suggestions(
        provider=ScriptedProvider(model_ref="scripted",
                                  payload=RuntimeError("api 503")),
        coder_id="llm-c", provision_id="ost-test:1",
    )
    assert outcomes[0].status == "failed"
    assert "api 503" in outcomes[0].detail


def test_codebook_required(tmp_db, tmp_path, monkeypatch):
    """If the codebook table is empty, the runner refuses."""
    monkeypatch.setattr(db_mod, "ROOT", tmp_path)
    ingest_mod.ingest_document(text=TEXT, **META)
    db_mod.add_coder("llm-c", name="Claude", role="llm",
                     is_human=False, model_ref="anthropic:claude-sonnet-4-6")
    from astralyzer.suggest.mock import MockProvider
    with pytest.raises(SuggestionError, match="codebook is empty"):
        run_suggestions(provider=MockProvider(), coder_id="llm-c",
                        provision_id="ost-test:1")


def test_llm_cannot_overwrite_human_draft(env):
    """Defense-in-depth: the runner only writes new suggested rows; even if
    a coder accidentally targets a provision with an adjudicated code, the
    suggestion is an independent row, not a write to the gold record."""
    from astralyzer.suggest.mock import MockProvider
    # Adjudicate a human code first.
    with db_mod.open_conn() as conn:
        conn.execute(
            "INSERT INTO codes(id, provision_id, coder_id, status, source_span_ref) "
            "VALUES('h1','ost-test:1','rs','adjudicated','x')"
        )
        conn.commit()
    outcomes = run_suggestions(provider=MockProvider(), coder_id="llm-c",
                               provision_id="ost-test:1")
    assert outcomes[0].status == "suggested"
    with db_mod.open_conn() as conn:
        rows = list(conn.execute(
            "SELECT status, coder_id FROM codes WHERE provision_id='ost-test:1' ORDER BY status"
        ))
        # The gold record is untouched; a suggestion now sits alongside it.
        assert {(r["status"], r["coder_id"]) for r in rows} == {
            ("adjudicated", "rs"), ("suggested", "llm-c"),
        }


def test_anthropic_provider_raises_without_api_key(monkeypatch):
    """Constructing the provider without the env var must fail loudly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from astralyzer.suggest.provider import build_provider
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_provider("anthropic")
