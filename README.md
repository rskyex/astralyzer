# astralyzer

Interpretive Authority Mapping Engine — analysis core for the Space Mandate Atlas.

Local-first, reproducible coding pipeline that turns governance documents (space, nuclear,
cyber, AI) into a coded, citable dataset measuring how undefined operative terms allocate
interpretive authority across instruments.

The methodological invariants this codebase enforces are in `docs/methodology.md`. Read
that file before changing the schema.

## Phase 1 — schema and storage

Storage layer only. No ingestion, no review UI, no LLM suggestions yet. Subsequent phases
are gated on review.

### Setup

    python -m venv .venv && . .venv/bin/activate
    pip install -e ".[dev]"

### Initialize the database

    astralyzer db init
    astralyzer db status

The DB lives at `data/astralyzer.db` by default (override with `ASTRALYZER_DB_PATH`). The
DB file is gitignored — the source of truth in version control is `migrations/` and
`codebook/codebook.yaml`.

### Load the codebook

    astralyzer codebook load codebook/codebook.yaml
    astralyzer codebook show operative_function

### Register coders

Handles are write-once and never reassigned to a different person.

    astralyzer coder add rs --name "R. Sky" --role lead
    astralyzer coder add llm-claude --name "Claude Suggester" --role llm \
        --llm --model anthropic:claude-opus-4-7

### Tests

    pytest
