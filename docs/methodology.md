# Methodological constraints

Five non-negotiable invariants govern this codebase. Each is enforced as close to the data
as possible — preferably in the schema, so app bugs cannot violate them.

1. **Human-adjudicated codes are the single source of truth.** LLM-generated codes exist
   only as `status='suggested'`. Enforced by triggers `trg_codes_llm_suggested_only_ins`
   and `trg_codes_llm_suggested_only_upd` on the `codes` table: any insert or update with
   a non-human `coder_id` and a status other than `suggested` is aborted at the DB layer.

2. **Every code links to (a) the exact source span and (b) the coder + timestamp.**
   Enforced by `NOT NULL` on `codes.source_span_ref`, `codes.coder_id`, and
   `codes.created_at`. The default FK behaviour on `codes.coder_id` is `RESTRICT`, so a
   coder with attributed codes cannot be deleted.

3. **Inter-coder reliability is computed from independent human double-coding only.**
   `reliability_runs` requires `coder_a_id != coder_b_id`; the reliability computation
   (Phase 5) additionally filters on `coders.is_human = 1`.

4. **No fabrication.** Every `documents` row requires `raw_text_sha256` and
   `retrieval_date` (`NOT NULL`). Every `provisions` row carries `char_start`/`char_end`
   offsets into the source text. Ingestion (Phase 2) will refuse to create a `documents`
   row without these.

5. **Versioned, citable dataset.** `dataset_versions.version` is the primary key, so a
   released version is write-once — re-using it raises `IntegrityError`. Each release
   row carries `git_commit_sha`, `generated_at`, and a `manifest_json` snapshot of the
   contributing documents (id, sha256, retrieval_date). Working (mid-iteration) exports
   embed the same manifest in their output files but are not recorded as releases.

## Schema-level invariants in `migrations/0001_initial_schema.sql`

- **One adjudicated code per provision.** Partial unique index
  `idx_codes_one_adjudicated_per_provision` on `codes(provision_id) WHERE status='adjudicated'`.
- **Coder handles are immutable.** `trg_coders_id_immutable` rejects any `UPDATE` of
  `coders.id`. Names can be corrected; identities cannot be reassigned.
- **Controlled vocabularies are `CHECK`-constrained at write time.** `status`,
  `operative_function`, `authority_default`, `verification_mechanism`, and
  `independent_epistemic_access` reject typos as `IntegrityError`.
- **`coupling_domains` is normalized.** `code_coupling_domains(code_id, domain)` with a
  `CHECK` over `{space, nuclear, cyber, ai}`. This keeps the headline analysis
  (`independent_epistemic_access` grouped by domain — the epistemic monopolisation map)
  on a clean `GROUP BY`.
- **Operative terms use a hybrid capture/canonicalize pattern.** `codes.operative_term_raw`
  is free text (preserves fidelity). `terms.canonical_label` is unique. `codes.term_id`
  is assigned at adjudication. A CHECK constraint requires that adjudicated rows with a
  non-null raw term also have a non-null `term_id` — canonicalization is mandatory before
  a row enters the gold record.
- **Confidence is suggestion-only.** A CHECK requires `suggested_confidence` to be NULL
  unless `status='suggested'`.

## Build phases

Phase 1 (this commit): schema, migration runner, codebook loader, coder registration.
Phase 2: ingestion + segmentation.
Phase 3: review UI (Flask + htmx).
Phase 4: LLM suggestion layer (behind a flag).
Phase 5: reliability (Cohen's kappa, per-field).
Phase 6: aggregation and versioned export.

Commit after each phase; review gate between phases.
