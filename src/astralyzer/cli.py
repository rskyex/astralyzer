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
review_app = typer.Typer(help="Local review UI.", no_args_is_help=True)
suggest_app = typer.Typer(help="LLM suggestion layer (opt-in).", no_args_is_help=True)
reliability_app = typer.Typer(help="Inter-coder reliability.", no_args_is_help=True)
export_app = typer.Typer(help="Versioned dataset export.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(codebook_app, name="codebook")
app.add_typer(coder_app, name="coder")
app.add_typer(ingest_app, name="ingest")
app.add_typer(review_app, name="review")
app.add_typer(suggest_app, name="suggest")
app.add_typer(reliability_app, name="reliability")
app.add_typer(export_app, name="export")


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


@suggest_app.command("run")
def suggest_run(
    coder: str = typer.Option(..., "--coder",
                              help="LLM coder handle (must be registered with --llm)."),
    provider: str = typer.Option("mock", "--provider",
                                 help="anthropic | mock (default: mock — opt into anthropic explicitly)."),
    model: str = typer.Option(None, "--model",
                              help="Provider-specific model id; defaults per provider."),
    provision: str = typer.Option(None, "--provision",
                                  help="Run on a single provision id."),
    document: str = typer.Option(None, "--document",
                                 help="Run on every provision in a document."),
    all_: bool = typer.Option(False, "--all",
                              help="Run on every provision in every document."),
    limit: int = typer.Option(None, "--limit",
                              help="Cap the number of provisions processed."),
    regenerate: bool = typer.Option(
        False, "--regenerate",
        help="Replace existing suggestions from this coder instead of skipping."),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Don't call the provider; report what would be processed."),
) -> None:
    """Generate suggested codes. Suggestions are stored as status='suggested'
    and never enter the gold record automatically — they require human review."""
    from astralyzer.suggest.provider import build_provider
    from astralyzer.suggest.runner import SuggestionError, run_suggestions

    selectors = [bool(provision), bool(document), all_]
    if sum(selectors) != 1:
        typer.echo("specify exactly one of --provision, --document, --all", err=True)
        raise typer.Exit(2)

    try:
        prov = build_provider(provider, model)
    except Exception as e:
        typer.echo(f"provider error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"# astralyzer suggest  provider={provider}  model_ref={prov.model_ref}  coder={coder}")
    if provider != "mock" and not dry_run:
        typer.echo("# (this will make real API calls — set --dry-run to inspect targets first)")

    try:
        outcomes = run_suggestions(
            provider=prov,
            coder_id=coder,
            provision_id=provision,
            document_id=document,
            all_=all_,
            limit=limit,
            regenerate=regenerate,
            dry_run=dry_run,
        )
    except SuggestionError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)

    n_ok = sum(1 for o in outcomes if o.status == "suggested")
    n_skip = sum(1 for o in outcomes if o.status == "skipped")
    n_fail = sum(1 for o in outcomes if o.status == "failed")
    n_dry = sum(1 for o in outcomes if o.status == "dry-run")
    for o in outcomes:
        line = f"  [{o.status:>9}] {o.provision_id}"
        if o.detail:
            line += f"  — {o.detail}"
        typer.echo(line)
    typer.echo(
        f"# done: suggested={n_ok} skipped={n_skip} failed={n_fail} dry_run={n_dry}"
    )


@review_app.command("serve")
def review_serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(5000, "--port"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Run the local review UI."""
    from astralyzer.review.app import create_app

    flask_app = create_app()
    typer.echo(f"astralyzer review UI: http://{host}:{port}/")
    flask_app.run(host=host, port=port, debug=debug)


@reliability_app.command("sample")
def reliability_sample(
    run_id: str = typer.Argument(..., help="Stable id for this run, e.g. 'rel-2026-05-24-a'."),
    coder_a: str = typer.Option(..., "--coder-a"),
    coder_b: str = typer.Option(..., "--coder-b"),
    document: str = typer.Option(None, "--document",
                                 help="Restrict sampling pool to this document."),
    n: int = typer.Option(10, "--n", help="Sample size."),
    seed: int = typer.Option(None, "--seed", help="Random seed for reproducibility."),
    provisions: str = typer.Option(
        None, "--provisions",
        help="Comma-separated provision ids; overrides random sampling."),
) -> None:
    """Create a reliability run with a sampled set of provisions."""
    from astralyzer import reliability as rel

    if provisions:
        sample = [p.strip() for p in provisions.split(",") if p.strip()]
    else:
        try:
            sample = rel.select_sample(document_id=document, n=n, seed=seed)
        except rel.ReliabilityError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(1)
    try:
        rel.create_run(run_id=run_id, coder_a_id=coder_a, coder_b_id=coder_b,
                       sample_provisions=sample)
    except rel.ReliabilityError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"created reliability run {run_id}: "
               f"coder_a={coder_a} coder_b={coder_b} sample={len(sample)}")
    for pid in sample:
        typer.echo(f"  {pid}")


@reliability_app.command("list")
def reliability_list() -> None:
    """List all reliability runs."""
    from astralyzer import reliability as rel
    runs = rel.list_runs()
    if not runs:
        typer.echo("No reliability runs.")
        return
    for r in runs:
        typer.echo(
            f"{r['id']:30}  {r['status']:9}  "
            f"a={r['coder_a_id']:6}  b={r['coder_b_id']:6}  "
            f"created={r['created_at']}"
        )


@reliability_app.command("show")
def reliability_show(run_id: str = typer.Argument(...)) -> None:
    """Show a run: status, sample, kappa (if computed)."""
    from astralyzer import reliability as rel
    run = rel.get_run(run_id)
    if run is None:
        typer.echo(f"no such run: {run_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"# {run['id']}")
    typer.echo(f"  status:  {run['status']}")
    typer.echo(f"  coder_a: {run['coder_a_id']}")
    typer.echo(f"  coder_b: {run['coder_b_id']}")
    typer.echo(f"  sample:  {len(run['sample_provisions'])} provisions")
    if run["status"] == "computed":
        typer.echo("  kappa:")
        for field, k in run["per_field_kappa"].items():
            p = run["percent_agreement"].get(field)
            k_str = f"{k:.3f}" if k is not None else "—"
            p_str = f"{p * 100:.0f}%" if p is not None else "—"
            typer.echo(f"    {field:35}  k={k_str:>7}  agr={p_str:>5}")


@reliability_app.command("compute")
def reliability_compute(run_id: str = typer.Argument(...)) -> None:
    """Compute Cohen's kappa + percent agreement; lift blinding."""
    from astralyzer import reliability as rel
    try:
        result = rel.compute_run(run_id)
    except rel.ReliabilityError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"computed {result.run_id}: "
               f"pairs={result.n_pairs}  missing={result.n_missing}")
    for field, k in result.kappa.items():
        p = result.agreement.get(field)
        k_str = f"{k:.3f}" if k is not None else "—"
        p_str = f"{p * 100:.0f}%" if p is not None else "—"
        typer.echo(f"  {field:35}  k={k_str:>7}  agr={p_str:>5}")


@export_app.command("release")
def export_release_cmd(
    version: str = typer.Argument(..., help="Semantic version tag, e.g. '0.1.0'."),
    out: Path = typer.Option(None, "--out", help="Override output directory."),
    notes: str = typer.Option(None, "--notes"),
    no_figures: bool = typer.Option(False, "--no-figures",
                                    help="Skip PDF figures (still emits CSV tables)."),
) -> None:
    """Released export. Registers in dataset_versions (write-once)."""
    from astralyzer.export import ExportError, export_release
    try:
        result = export_release(version, out_dir=out, notes=notes,
                                with_figures=not no_figures)
    except ExportError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"released {result['version']}: {result['n_codes']} adjudicated codes "
        f"→ {result['target']}"
    )


@export_app.command("wip")
def export_wip_cmd(
    out: Path = typer.Option(None, "--out", help="Override output directory."),
    no_figures: bool = typer.Option(False, "--no-figures"),
) -> None:
    """Working export. Not registered in dataset_versions."""
    from astralyzer.export import export_wip
    result = export_wip(out_dir=out, with_figures=not no_figures)
    typer.echo(
        f"wip export: {result['n_codes']} adjudicated codes "
        f"→ {result['target']}"
    )


@export_app.command("list")
def export_list_cmd() -> None:
    """List registered (released) dataset versions."""
    from astralyzer.export import list_versions
    rows = list_versions()
    if not rows:
        typer.echo("No released versions.")
        return
    for r in rows:
        sha = (r.get("git_commit_sha") or "")[:7]
        typer.echo(
            f"{r['version']:12}  {r['generated_at']}  sha={sha}  "
            f"docs={r['n_documents']:>3} prov={r['n_provisions']:>4} "
            f"adj={r['n_adjudicated_codes']:>4}"
            + (f"  — {r['notes']}" if r.get("notes") else "")
        )


@app.command("analyze")
def analyze_cmd(
    out: Path = typer.Option(None, "--out",
                             help="Output dir; default data/analysis/<utc-stamp>/."),
    no_figures: bool = typer.Option(False, "--no-figures"),
) -> None:
    """Write aggregations (and figures) without a full export."""
    from datetime import datetime, timezone
    from astralyzer import analysis as analysis_mod
    from astralyzer.db import ROOT
    if out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = ROOT / "data" / "analysis" / stamp
    paths = analysis_mod.write_tables(out)
    typer.echo(f"wrote {len(paths)} table(s) → {out}")
    for p in paths:
        typer.echo(f"  {p.name}")
    if not no_figures:
        try:
            figs = analysis_mod.write_figures(out)
            typer.echo(f"wrote {len(figs)} figure(s)")
            for p in figs:
                typer.echo(f"  {p.name}")
        except RuntimeError as e:
            typer.echo(f"# figures skipped: {e}", err=True)


if __name__ == "__main__":
    app()
