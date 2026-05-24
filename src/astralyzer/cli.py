"""Command-line entry point for astralyzer."""
from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path

import typer

from astralyzer import codebook as codebook_mod
from astralyzer import db
from astralyzer import ingest as ingest_mod


class InstrumentType(str, Enum):
    space = "space"
    nuclear = "nuclear"
    cyber = "cyber"
    ai = "ai"
    cross = "cross"


app = typer.Typer(help="Astralyzer — Interpretive Authority Mapping Engine.",
                  no_args_is_help=True)
db_app = typer.Typer(help="Database operations.", no_args_is_help=True)
codebook_app = typer.Typer(help="Codebook operations.", no_args_is_help=True)
coder_app = typer.Typer(help="Coder management.", no_args_is_help=True)
ingest_app = typer.Typer(help="Document ingestion and segmentation.",
                         no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(codebook_app, name="codebook")
app.add_typer(coder_app, name="coder")
app.add_typer(ingest_app, name="ingest")


@db_app.command("init")
def db_init() -> None:
    """Apply all pending migrations."""
    applied = db.migrate()
    if not applied:
        typer.echo("No migrations to apply; database is up to date.")
        return
    for v in applied:
        typer.echo(f"Applied migration {v}")


@db_app.command("status")
def db_status() -> None:
    """List applied migrations."""
    rows = db.applied_migrations()
    if not rows:
        typer.echo("No migrations applied. Run `astralyzer db init`.")
        return
    for v, ts in rows:
        typer.echo(f"{v}  applied_at={ts}")


@codebook_app.command("load")
def codebook_load(
    path: Path = typer.Argument(..., exists=True, readable=True,
                                help="Path to codebook YAML.")
) -> None:
    """Upsert codebook fields from a YAML file."""
    n = codebook_mod.load_from_yaml(path)
    typer.echo(f"Upserted {n} codebook field(s) from {path}")


@codebook_app.command("show")
def codebook_show(
    field: str = typer.Argument(None, help="Field name, or omit to list all."),
) -> None:
    """Show a codebook field, or list all fields."""
    if field is None:
        for f in codebook_mod.list_fields():
            typer.echo(f"{f['field']}  (version {f['version']})")
        return
    f = codebook_mod.get_field(field)
    if f is None:
        typer.echo(f"No such field: {field}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(f, indent=2))


@coder_app.command("add")
def coder_add(
    handle: str = typer.Argument(..., help="Stable handle (write-once)."),
    name: str = typer.Option(..., "--name"),
    role: str = typer.Option(..., "--role"),
    llm: bool = typer.Option(False, "--llm", help="Mark as an LLM coder."),
    model: str = typer.Option(None, "--model",
                              help="LLM model ref, required with --llm."),
) -> None:
    """Register a coder. Handles are write-once and never reassigned."""
    if llm and not model:
        typer.echo("--model is required with --llm", err=True)
        raise typer.Exit(2)
    if not llm and model:
        typer.echo("--model is only meaningful with --llm", err=True)
        raise typer.Exit(2)
    db.add_coder(handle=handle, name=name, role=role,
                 is_human=not llm, model_ref=model)
    typer.echo(f"Added coder {handle} ({'llm' if llm else 'human'})")


@ingest_app.command("add")
def ingest_add(
    document_id: str = typer.Argument(..., help="Stable slug, e.g. 'ost-1967'."),
    short_name: str = typer.Option(..., "--short-name"),
    full_title: str = typer.Option(..., "--full-title"),
    instrument_type: InstrumentType = typer.Option(..., "--instrument-type"),
    source_url: str = typer.Option(..., "--source-url"),
    version_or_date: str = typer.Option(..., "--version-or-date"),
    retrieval_date: str = typer.Option(..., "--retrieval-date",
                                       help="ISO-8601 date of retrieval."),
    file: Path = typer.Option(None, "--file", exists=True, readable=True,
                              help="Path to the source text file."),
    use_stdin: bool = typer.Option(False, "--stdin",
                                   help="Read source text from stdin."),
    segments_file: Path = typer.Option(None, "--segments", exists=True, readable=True,
                                       help="YAML override: list of {anchor, char_start}."),
    single_provision: bool = typer.Option(False, "--single-provision",
                                          help="Treat the entire document as one provision."),
    notes: str = typer.Option(None, "--notes"),
) -> None:
    """Ingest a document, segment it into provisions, write raw text to data/raw/."""
    if file is None and not use_stdin:
        typer.echo("Provide either --file <path> or --stdin", err=True)
        raise typer.Exit(2)
    if file is not None and use_stdin:
        typer.echo("--file and --stdin are mutually exclusive", err=True)
        raise typer.Exit(2)
    if segments_file is not None and single_provision:
        typer.echo("--segments and --single-provision are mutually exclusive", err=True)
        raise typer.Exit(2)

    text = sys.stdin.read() if use_stdin else file.read_text(encoding="utf-8")
    overrides = ingest_mod.load_segments_yaml(segments_file) if segments_file else None

    try:
        result = ingest_mod.ingest_document(
            document_id=document_id,
            short_name=short_name,
            full_title=full_title,
            instrument_type=instrument_type.value,
            official_source_url=source_url,
            version_or_date=version_or_date,
            retrieval_date=retrieval_date,
            text=text,
            segments_override=overrides,
            single_provision=single_provision,
            notes=notes,
        )
    except ingest_mod.IngestError as e:
        typer.echo(f"ingest error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"{result.status}: {result.document_id} "
        f"(sha={result.sha256[:12]}…, n_provisions={result.n_provisions})"
    )


@ingest_app.command("list")
def ingest_list() -> None:
    """List ingested documents."""
    rows = ingest_mod.list_documents()
    if not rows:
        typer.echo("No documents ingested.")
        return
    for r in rows:
        typer.echo(
            f"{r['id']:30}  {r['instrument_type']:8}  "
            f"v={r['version_or_date']:12}  ret={r['retrieval_date']}  "
            f"sha={r['raw_text_sha256'][:12]}…"
        )


@ingest_app.command("show")
def ingest_show(document_id: str = typer.Argument(...)) -> None:
    """Show a document's metadata and its segmented provisions."""
    info = ingest_mod.show_document(document_id)
    if info is None:
        typer.echo(f"No such document: {document_id}", err=True)
        raise typer.Exit(1)
    d = info["document"]
    typer.echo(f"# {d['id']} — {d['full_title']}")
    typer.echo(f"  instrument:       {d['instrument_type']}")
    typer.echo(f"  source_url:       {d['official_source_url']}")
    typer.echo(f"  version_or_date:  {d['version_or_date']}")
    typer.echo(f"  retrieval_date:   {d['retrieval_date']}")
    typer.echo(f"  raw_text:         {d['raw_text_ref']}")
    typer.echo(f"  sha256:           {d['raw_text_sha256']}")
    typer.echo(f"  provisions:       {len(info['provisions'])}")
    for p in info["provisions"]:
        snippet = p["text"].replace("\n", " ")[:80]
        typer.echo(
            f"    [{p['ordinal']:>3}] {p['citation_anchor']:20} "
            f"({p['char_start']}-{p['char_end']})  {snippet}"
        )


if __name__ == "__main__":
    app()
