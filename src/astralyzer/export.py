"""Versioned dataset export.

Released exports register in `dataset_versions` (PK on version → write-once)
and write a self-contained directory under data/exports/<version>/:

    codes.csv               — adjudicated codes with full provenance
    codes.json              — same, structured (coupling_domains as list)
    documents.csv           — every ingested document
    provisions.csv          — every provision
    terms.csv               — canonical vocabulary
    codebook.json           — codebook snapshot at export time
    manifest.json           — version, git sha, generated_at, document sha256s
    analysis/*.csv          — aggregations
    analysis/*.pdf          — figures (if matplotlib installed)

Working ("wip") exports write the same files into
data/exports/wip-<utc-timestamp>-<git-sha7>/ and do NOT register, but their
manifest.json still carries the git sha and timestamp so an intermediate
export is uniquely identifiable.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from astralyzer import analysis as analysis_mod
from astralyzer import db as db_mod
from astralyzer.db import open_conn


class ExportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------

def _git_sha() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=db_mod.ROOT, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Data pulls
# ---------------------------------------------------------------------------

def _adjudicated_codes_df() -> pd.DataFrame:
    with open_conn() as conn:
        codes = pd.read_sql_query(
            """
            SELECT
              c.id AS code_id,
              p.document_id,
              d.short_name AS document_short_name,
              d.instrument_type,
              p.citation_anchor,
              p.id AS provision_id,
              p.char_start, p.char_end,
              c.coder_id,
              c.updated_at AS adjudicated_at,
              c.operative_term_raw,
              c.term_id,
              t.canonical_label AS term_canonical_label,
              c.operative_function,
              c.authority_default,
              c.verification_mechanism,
              c.independent_epistemic_access,
              c.ai_operation_effect,
              c.source_span_ref,
              c.rationale
            FROM codes c
            JOIN provisions p ON c.provision_id = p.id
            JOIN documents d ON p.document_id = d.id
            LEFT JOIN terms t ON c.term_id = t.id
            WHERE c.status = 'adjudicated'
            ORDER BY p.document_id, p.ordinal, c.id
            """,
            conn,
        )
        if codes.empty:
            codes["coupling_domains"] = pd.Series(dtype=object)
        else:
            cd = pd.read_sql_query(
                "SELECT code_id, domain FROM code_coupling_domains "
                "ORDER BY code_id, domain",
                conn,
            )
            grouped = cd.groupby("code_id")["domain"].apply(list).to_dict()
            codes["coupling_domains"] = codes["code_id"].map(
                lambda cid: grouped.get(cid, [])
            )
    return codes


def _documents_df() -> pd.DataFrame:
    with open_conn() as conn:
        return pd.read_sql_query("SELECT * FROM documents ORDER BY id", conn)


def _provisions_df() -> pd.DataFrame:
    with open_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, document_id, ordinal, citation_anchor, char_start, char_end "
            "FROM provisions ORDER BY document_id, ordinal",
            conn,
        )


def _terms_df() -> pd.DataFrame:
    with open_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, canonical_label, definition, created_by, created_at "
            "FROM terms ORDER BY id",
            conn,
        )


def _codebook() -> list[dict]:
    with open_conn() as conn:
        rows = conn.execute(
            "SELECT field, definition, decision_rules, examples, value_domain, "
            "       version, updated_at "
            "FROM codebook_fields ORDER BY field"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("examples", "value_domain"):
            if d.get(k):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


def _count(table: str) -> int:
    with open_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def _build_manifest(
    *, version: str | None, is_release: bool,
    docs_df: pd.DataFrame, n_codes: int,
) -> dict:
    return {
        "version": version,
        "is_release": is_release,
        "generated_at": _utc_now_iso(),
        "git_commit_sha": _git_sha(),
        "counts": {
            "documents": int(len(docs_df)),
            "provisions": _count("provisions"),
            "adjudicated_codes": n_codes,
        },
        "documents": [
            {
                "id": r["id"],
                "short_name": r["short_name"],
                "full_title": r["full_title"],
                "instrument_type": r["instrument_type"],
                "official_source_url": r["official_source_url"],
                "version_or_date": r["version_or_date"],
                "retrieval_date": r["retrieval_date"],
                "raw_text_ref": r["raw_text_ref"],
                "raw_text_sha256": r["raw_text_sha256"],
            }
            for _, r in docs_df.iterrows()
        ],
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _write_codes(codes_df: pd.DataFrame, target: Path) -> None:
    # CSV with pipe-delimited coupling_domains; JSON keeps the list shape.
    csv_df = codes_df.copy()
    if "coupling_domains" in csv_df.columns:
        csv_df["coupling_domains"] = csv_df["coupling_domains"].apply(
            lambda v: "|".join(v) if isinstance(v, list) else ""
        )
    csv_df.to_csv(target / "codes.csv", index=False)

    records = codes_df.to_dict(orient="records")
    (target / "codes.json").write_text(
        json.dumps(records, indent=2, default=str), encoding="utf-8",
    )


def _write_all(
    *, version: str | None, is_release: bool, target: Path,
    with_figures: bool,
) -> tuple[Path, dict, int]:
    codes_df = _adjudicated_codes_df()
    docs_df = _documents_df()
    provs_df = _provisions_df()
    terms_df = _terms_df()
    codebook = _codebook()
    manifest = _build_manifest(
        version=version, is_release=is_release,
        docs_df=docs_df, n_codes=len(codes_df),
    )

    target.mkdir(parents=True, exist_ok=True)
    _write_codes(codes_df, target)
    docs_df.to_csv(target / "documents.csv", index=False)
    provs_df.to_csv(target / "provisions.csv", index=False)
    terms_df.to_csv(target / "terms.csv", index=False)
    (target / "codebook.json").write_text(
        json.dumps(codebook, indent=2, default=str), encoding="utf-8",
    )
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )

    analysis_dir = target / "analysis"
    analysis_mod.write_tables(analysis_dir)
    if with_figures:
        try:
            analysis_mod.write_figures(analysis_dir)
        except RuntimeError as e:
            (analysis_dir / "FIGURES_SKIPPED.txt").write_text(str(e) + "\n",
                                                              encoding="utf-8")

    return target, manifest, len(codes_df)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_release(
    version: str, *, out_dir: Path | None = None,
    notes: str | None = None, with_figures: bool = True,
) -> dict:
    target = out_dir or (db_mod.ROOT / "data" / "exports" / version)
    if target.exists() and any(target.iterdir()):
        # Don't surprise the user: an existing output dir for a released
        # version is almost always a re-run; the DB will reject it anyway.
        raise ExportError(
            f"output directory {target} is non-empty; refusing to overwrite. "
            "Remove the dir to retry, or choose a different version."
        )

    _, manifest, n_codes = _write_all(
        version=version, is_release=True, target=target, with_figures=with_figures,
    )

    try:
        with open_conn() as conn:
            conn.execute(
                "INSERT INTO dataset_versions("
                "version, git_commit_sha, n_documents, n_provisions, "
                "n_adjudicated_codes, manifest_json, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    version, manifest["git_commit_sha"],
                    manifest["counts"]["documents"],
                    manifest["counts"]["provisions"],
                    n_codes, json.dumps(manifest), notes,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError as e:
        raise ExportError(
            f"version {version!r} is already registered in dataset_versions "
            "(write-once). Choose a new version tag."
        ) from e

    return {"target": target, "version": version, "manifest": manifest,
            "n_codes": n_codes}


def export_wip(*, out_dir: Path | None = None,
               with_figures: bool = True) -> dict:
    sha = _git_sha() or "nogit"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir or (db_mod.ROOT / "data" / "exports"
                         / f"wip-{stamp}-{sha[:7]}")
    _, manifest, n_codes = _write_all(
        version=None, is_release=False, target=target,
        with_figures=with_figures,
    )
    return {"target": target, "manifest": manifest, "n_codes": n_codes}


def list_versions() -> list[dict]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT version, generated_at, git_commit_sha, "
            "       n_documents, n_provisions, n_adjudicated_codes, notes "
            "FROM dataset_versions ORDER BY version"
        )]
