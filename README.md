# astralyzer

Interpretive Authority Mapping Engine — analysis core for the Space Mandate Atlas.

Local-first, reproducible coding pipeline that turns governance documents (space, nuclear,
cyber, AI) into a coded, citable dataset measuring how undefined operative terms allocate
interpretive authority across instruments.

The methodological invariants this codebase enforces are in `docs/methodology.md`. Read
that file before changing the schema.

## Setup

    python -m venv .venv && . .venv/bin/activate
    pip install -e ".[dev]"

## Initialize the database

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

## Ingest a document

    astralyzer ingest add ost-1967 \
        --short-name "OST" \
        --full-title "Outer Space Treaty" \
        --instrument-type space \
        --source-url "https://www.unoosa.org/.../outerspacetreaty.html" \
        --version-or-date "1967-01-27" \
        --retrieval-date "2026-05-24" \
        --file /path/to/ost.txt

The default segmenter recognizes `Article I` / `Article 1` / `Art. 1` / `Section 1` / `§ 1`
markers at line start, and preserves any pre-marker prose as a `Preamble` segment.
Override with `--segments segs.yaml` (a list of `{anchor, char_start}`) or
`--single-provision` for documents with no internal structure to code at provision
granularity.

    astralyzer ingest list
    astralyzer ingest show ost-1967

## LLM suggestion layer (opt-in)

Suggestions are written as `status='suggested'` rows attributed to an LLM coder.
They are NEVER promoted into the gold record by code — a human must accept
and adjudicate them through the review UI.

Install the optional provider dep, register an LLM coder, then run:

    pip install -e ".[suggest]"
    export ANTHROPIC_API_KEY=...                   # never pass keys as flags
    astralyzer suggest run --coder llm-claude --document ost-1967 --dry-run
    astralyzer suggest run --coder llm-claude --document ost-1967

By default the provider is `mock` (deterministic, no API call, useful for
exercising the pipeline). Choose `--provider anthropic` to make real calls.
The default model is `claude-sonnet-4-6`; override with `--model`.

Idempotent: re-running skips provisions that already have a suggestion from
the chosen coder. `--regenerate` replaces them. `--limit N` caps the count.

## Run the review UI

    astralyzer review serve            # http://127.0.0.1:5000

Pick a coder from the header dropdown; you can then add draft codes on any provision,
edit your own drafts, and promote drafts to adjudicated. LLM coders are selectable
but cannot write drafts or adjudicate — they can only produce `suggested` codes
(Phase 4 will populate those automatically).

## Tests

    pytest
