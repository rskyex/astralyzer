-- 0002_reliability.sql
-- Phase 5: support for inter-coder reliability.
--
-- Adds (a) a `status` column on reliability_runs so the UI knows when
-- blinding can be lifted, (b) a normalized membership table so the review
-- UI can ask "is this provision blinded for me right now?" with a clean
-- JOIN, and (c) a trigger enforcing methodological constraint #3 from
-- docs/methodology.md: reliability is computed from human double-coding only.
--
-- The canonical sample_provisions JSON column on reliability_runs stays as
-- the immutable record; reliability_run_provisions is a denormalized index
-- populated at insert time.

BEGIN;

ALTER TABLE reliability_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'computed'));

CREATE TABLE reliability_run_provisions (
    run_id       TEXT NOT NULL REFERENCES reliability_runs(id) ON DELETE CASCADE,
    provision_id TEXT NOT NULL REFERENCES provisions(id),
    PRIMARY KEY (run_id, provision_id)
);
CREATE INDEX idx_rrp_provision ON reliability_run_provisions(provision_id);

CREATE TRIGGER trg_reliability_humans_only
BEFORE INSERT ON reliability_runs
FOR EACH ROW
WHEN (SELECT is_human FROM coders WHERE id = NEW.coder_a_id) = 0
  OR (SELECT is_human FROM coders WHERE id = NEW.coder_b_id) = 0
BEGIN
    SELECT RAISE(ABORT,
        'reliability runs require human coders on both sides (constraint #3)');
END;

INSERT INTO schema_migrations(version) VALUES ('0002_reliability');

COMMIT;
