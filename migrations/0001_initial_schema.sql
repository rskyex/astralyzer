-- 0001_initial_schema.sql
-- Phase 1 schema for the Interpretive Authority Mapping Engine.
-- The methodological constraints this schema enforces are documented in
-- docs/methodology.md. Do not relax a constraint here without updating that file.
--
-- The schema_migrations table is bootstrap-managed by src/astralyzer/db.py; it is
-- created (with IF NOT EXISTS) before any migration runs.

BEGIN;

-- ---------------------------------------------------------------------------
-- documents: ingested source instruments. Provenance is mandatory.
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
    id                  TEXT PRIMARY KEY,                  -- stable slug, e.g. 'ost-1967'
    short_name          TEXT NOT NULL UNIQUE,
    full_title          TEXT NOT NULL,
    instrument_type     TEXT NOT NULL CHECK (instrument_type IN
                            ('space','nuclear','cyber','ai','cross')),
    official_source_url TEXT NOT NULL,
    version_or_date     TEXT NOT NULL,                     -- as published
    retrieval_date      TEXT NOT NULL,                     -- ISO-8601
    raw_text_ref        TEXT NOT NULL,                     -- path under data/raw/
    raw_text_sha256     TEXT NOT NULL,                     -- content hash, reproducibility
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- provisions: article/section-level units. id is deterministic so re-ingestion
-- is idempotent and exported row IDs are stable across runs.
-- ---------------------------------------------------------------------------
CREATE TABLE provisions (
    id              TEXT PRIMARY KEY,                      -- "<document_id>:<ordinal>"
    document_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal         INTEGER NOT NULL,
    citation_anchor TEXT NOT NULL,                         -- e.g. "Art. IX", "§ 3(b)"
    text            TEXT NOT NULL,
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL,
    UNIQUE (document_id, ordinal),
    CHECK (char_end >= char_start)
);
CREATE INDEX idx_provisions_doc ON provisions(document_id, ordinal);

-- ---------------------------------------------------------------------------
-- coders: human or LLM identities. Handles (id) are write-once.
-- ---------------------------------------------------------------------------
CREATE TABLE coders (
    id         TEXT PRIMARY KEY,                           -- handle, e.g. 'rs'
    name       TEXT NOT NULL,
    role       TEXT NOT NULL,
    is_human   INTEGER NOT NULL CHECK (is_human IN (0,1)),
    model_ref  TEXT,                                       -- e.g. 'anthropic:claude-opus-4-7'
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER trg_coders_id_immutable
BEFORE UPDATE OF id ON coders
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'coder.id is write-once and may not be reassigned');
END;

-- ---------------------------------------------------------------------------
-- terms: canonical vocabulary for operative terms. Emergent — grows during
-- adjudication as new canonical labels are introduced.
-- ---------------------------------------------------------------------------
CREATE TABLE terms (
    id              TEXT PRIMARY KEY,                      -- slug, e.g. 'peaceful-purposes'
    canonical_label TEXT NOT NULL UNIQUE,
    definition      TEXT,
    created_by      TEXT REFERENCES coders(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- codes: the coded values. Three statuses:
--   suggested    — LLM-produced, never written into the gold record automatically
--   human_draft  — human-coded but not yet adjudicated
--   adjudicated  — gold record; at most one per provision
-- ---------------------------------------------------------------------------
CREATE TABLE codes (
    id                           TEXT PRIMARY KEY,
    provision_id                 TEXT NOT NULL REFERENCES provisions(id) ON DELETE CASCADE,
    coder_id                     TEXT NOT NULL REFERENCES coders(id),
    status                       TEXT NOT NULL CHECK (status IN
                                     ('suggested','human_draft','adjudicated')),
    operative_term_raw           TEXT,                     -- verbatim from provision
    term_id                      TEXT REFERENCES terms(id),-- canonical, set at adjudication
    operative_function           TEXT CHECK (operative_function IS NULL OR operative_function IN
                                     ('permits','restricts','triggers_notification',
                                      'defines_zone','allocates_right','assigns_authority','other')),
    authority_default            TEXT CHECK (authority_default IS NULL OR authority_default IN
                                     ('operating_party','launching_state','consensus_body',
                                      'unspecified','other')),
    verification_mechanism       TEXT CHECK (verification_mechanism IS NULL OR verification_mechanism IN
                                     ('none','notification','inspection',
                                      'third_party_monitoring','data_sharing','other')),
    independent_epistemic_access INTEGER CHECK (independent_epistemic_access IS NULL
                                                OR independent_epistemic_access IN (0,1,2)),
    ai_operation_effect          TEXT,
    source_span_ref              TEXT NOT NULL,
    rationale                    TEXT,
    suggested_confidence         REAL CHECK (suggested_confidence IS NULL
                                             OR (suggested_confidence >= 0 AND suggested_confidence <= 1)),
    created_at                   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                   TEXT NOT NULL DEFAULT (datetime('now')),
    -- Adjudicated rows with a raw term must canonicalize to a term_id.
    CHECK (status != 'adjudicated' OR operative_term_raw IS NULL OR term_id IS NOT NULL),
    -- Confidence is only meaningful on suggestions.
    CHECK (suggested_confidence IS NULL OR status = 'suggested')
);
CREATE INDEX idx_codes_provision ON codes(provision_id);
CREATE INDEX idx_codes_status    ON codes(status);
CREATE INDEX idx_codes_coder     ON codes(coder_id);
CREATE INDEX idx_codes_term      ON codes(term_id);

-- Exactly one adjudicated (gold) code per provision.
CREATE UNIQUE INDEX idx_codes_one_adjudicated_per_provision
    ON codes(provision_id) WHERE status = 'adjudicated';

-- LLM coders may ONLY produce status='suggested'. Hard-locks methodological constraint #1.
CREATE TRIGGER trg_codes_llm_suggested_only_ins
BEFORE INSERT ON codes
FOR EACH ROW
WHEN (SELECT is_human FROM coders WHERE id = NEW.coder_id) = 0
     AND NEW.status != 'suggested'
BEGIN
    SELECT RAISE(ABORT, 'LLM coders may only produce status=suggested codes');
END;

CREATE TRIGGER trg_codes_llm_suggested_only_upd
BEFORE UPDATE ON codes
FOR EACH ROW
WHEN (SELECT is_human FROM coders WHERE id = NEW.coder_id) = 0
     AND NEW.status != 'suggested'
BEGIN
    SELECT RAISE(ABORT, 'LLM coders may only produce status=suggested codes');
END;

CREATE TRIGGER trg_codes_updated_at
AFTER UPDATE ON codes
FOR EACH ROW
BEGIN
    UPDATE codes SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- ---------------------------------------------------------------------------
-- code_coupling_domains: normalized multi-select. Querying
-- independent_epistemic_access GROUP BY domain (the epistemic monopolisation
-- map) joins through here.
-- ---------------------------------------------------------------------------
CREATE TABLE code_coupling_domains (
    code_id TEXT NOT NULL REFERENCES codes(id) ON DELETE CASCADE,
    domain  TEXT NOT NULL CHECK (domain IN ('space','nuclear','cyber','ai')),
    PRIMARY KEY (code_id, domain)
);
CREATE INDEX idx_ccd_domain ON code_coupling_domains(domain);

-- ---------------------------------------------------------------------------
-- term_migrations: how operative terms move across instruments. References
-- the canonical term where one exists, with a free-text fallback for
-- pre-canonicalization tracking.
-- ---------------------------------------------------------------------------
CREATE TABLE term_migrations (
    id                TEXT PRIMARY KEY,
    term_id           TEXT REFERENCES terms(id),
    term_raw          TEXT,
    from_provision_id TEXT NOT NULL REFERENCES provisions(id),
    to_provision_id   TEXT NOT NULL REFERENCES provisions(id),
    note              TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (term_id IS NOT NULL OR term_raw IS NOT NULL)
);
CREATE INDEX idx_term_migrations_term ON term_migrations(term_id);

-- ---------------------------------------------------------------------------
-- codebook_fields: living codebook loaded from codebook/codebook.yaml.
-- ---------------------------------------------------------------------------
CREATE TABLE codebook_fields (
    field          TEXT PRIMARY KEY,
    definition     TEXT NOT NULL,
    decision_rules TEXT NOT NULL,
    examples       TEXT,                                   -- JSON array
    value_domain   TEXT,                                   -- JSON array for controlled vocabs
    version        TEXT NOT NULL,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- reliability_runs: independent double-coding records.
-- ---------------------------------------------------------------------------
CREATE TABLE reliability_runs (
    id                TEXT PRIMARY KEY,
    coder_a_id        TEXT NOT NULL REFERENCES coders(id),
    coder_b_id        TEXT NOT NULL REFERENCES coders(id),
    sample_provisions TEXT NOT NULL,                       -- JSON array of provision ids
    per_field_kappa   TEXT,                                -- JSON {field: kappa}
    percent_agreement TEXT,                                -- JSON {field: percent}
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (coder_a_id != coder_b_id)
);

-- ---------------------------------------------------------------------------
-- dataset_versions: write-once release ledger. Re-using a version raises
-- IntegrityError (PK collision).
-- ---------------------------------------------------------------------------
CREATE TABLE dataset_versions (
    version             TEXT PRIMARY KEY,                  -- semver, e.g. '0.1.0'
    generated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    git_commit_sha      TEXT,
    n_documents         INTEGER NOT NULL,
    n_provisions        INTEGER NOT NULL,
    n_adjudicated_codes INTEGER NOT NULL,
    manifest_json       TEXT NOT NULL,                     -- snapshot of documents
    notes               TEXT
);

INSERT INTO schema_migrations(version) VALUES ('0001_initial_schema');

COMMIT;
