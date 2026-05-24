"""Command-line entry point for astralyzer.

Phase 1 commands only: db init/status, codebook load/show, coder add.
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from astralyzer import codebook as codebook_mod
from astralyzer import db

app = typer.Typer(help="Astralyzer — Interpretive Authority Mapping Engine.",
                  no_args_is_help=True)
db_app = typer.Typer(help="Database operations.", no_args_is_help=True)
codebook_app = typer.Typer(help="Codebook operations.", no_args_is_help=True)
coder_app = typer.Typer(help="Coder management.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(codebook_app, name="codebook")
app.add_typer(coder_app, name="coder")


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


if __name__ == "__main__":
    app()
