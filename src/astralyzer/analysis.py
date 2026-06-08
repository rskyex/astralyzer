"""Aggregations and publication figures.

Operates exclusively over `status='adjudicated'` rows — the gold record.
Suggestions and human drafts are explicitly excluded from analysis.

Headline aggregation (the "epistemic monopolisation map"):
  independent_epistemic_access × instrument_type
  independent_epistemic_access × coupling_domain

Also: authority_default distribution and term occurrences/migrations.

Tables (CSV) always produced. Figures (PDF) require matplotlib, which is in
the optional `figures` extra; analysis runs without matplotlib emit tables only.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from astralyzer.db import open_conn


# ---------------------------------------------------------------------------
# Raw data pulls
# ---------------------------------------------------------------------------

def _adjudicated_codes_long() -> pd.DataFrame:
    """One row per (code, domain). Codes with no coupling domains appear once
    with coupling_domain=NaN, so per-instrument aggregations don't drop them."""
    with open_conn() as conn:
        return pd.read_sql_query(
            """
            SELECT
              c.id AS code_id,
              d.id AS document_id,
              d.instrument_type,
              c.operative_function,
              c.authority_default,
              c.verification_mechanism,
              c.independent_epistemic_access,
              c.term_id,
              t.canonical_label AS term_canonical_label,
              cd.domain AS coupling_domain
            FROM codes c
            JOIN provisions p ON c.provision_id = p.id
            JOIN documents d ON p.document_id = d.id
            LEFT JOIN terms t ON c.term_id = t.id
            LEFT JOIN code_coupling_domains cd ON cd.code_id = c.id
            WHERE c.status = 'adjudicated'
            """,
            conn,
        )


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def iea_by_instrument() -> pd.DataFrame:
    """Cross-tab: rows = instrument_type, cols = IEA value (0, 1, 2 + NaN).
    Each adjudicated code contributes exactly once."""
    df = _adjudicated_codes_long()
    if df.empty:
        return pd.DataFrame()
    by_code = df.drop_duplicates(subset=["code_id"])
    return pd.crosstab(
        by_code["instrument_type"],
        by_code["independent_epistemic_access"],
        dropna=False,
    )


def iea_by_domain() -> pd.DataFrame:
    """Cross-tab: rows = coupling_domain, cols = IEA value. A code with two
    coupling domains counts in both rows — this is intentional, since the
    epistemic monopolisation map is about how each domain looks."""
    df = _adjudicated_codes_long()
    if df.empty:
        return pd.DataFrame()
    df = df.dropna(subset=["coupling_domain"])
    if df.empty:
        return pd.DataFrame()
    return pd.crosstab(
        df["coupling_domain"],
        df["independent_epistemic_access"],
        dropna=False,
    )


def authority_default_distribution() -> pd.DataFrame:
    df = _adjudicated_codes_long()
    if df.empty:
        return pd.DataFrame(columns=["authority_default", "count"])
    by_code = df.drop_duplicates(subset=["code_id"])
    return (by_code["authority_default"]
            .value_counts(dropna=False)
            .rename_axis("authority_default")
            .reset_index(name="count"))


def authority_default_by_instrument() -> pd.DataFrame:
    df = _adjudicated_codes_long()
    if df.empty:
        return pd.DataFrame()
    by_code = df.drop_duplicates(subset=["code_id"])
    return pd.crosstab(
        by_code["instrument_type"],
        by_code["authority_default"],
        dropna=False,
    )


def term_occurrences() -> pd.DataFrame:
    """Per (canonical term, provision) the adjudicated occurrences. Raw
    material for the term-migration graph."""
    with open_conn() as conn:
        return pd.read_sql_query(
            """
            SELECT t.id AS term_id,
                   t.canonical_label,
                   d.id AS document_id,
                   d.instrument_type,
                   p.id AS provision_id,
                   p.citation_anchor
            FROM codes c
            JOIN terms t ON c.term_id = t.id
            JOIN provisions p ON c.provision_id = p.id
            JOIN documents d ON p.document_id = d.id
            WHERE c.status = 'adjudicated' AND c.term_id IS NOT NULL
            ORDER BY t.id, d.id, p.ordinal
            """,
            conn,
        )


def term_migration_edges() -> pd.DataFrame:
    """User-asserted term migrations from the term_migrations table."""
    with open_conn() as conn:
        return pd.read_sql_query(
            "SELECT id, term_id, term_raw, from_provision_id, to_provision_id, note "
            "FROM term_migrations ORDER BY id",
            conn,
        )


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_tables(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    pairs = [
        ("iea_by_instrument.csv", iea_by_instrument(), True),
        ("iea_by_domain.csv", iea_by_domain(), True),
        ("authority_default_distribution.csv", authority_default_distribution(), False),
        ("authority_default_by_instrument.csv", authority_default_by_instrument(), True),
        ("term_occurrences.csv", term_occurrences(), False),
        ("term_migrations.csv", term_migration_edges(), False),
    ]
    for name, df, write_index in pairs:
        p = out_dir / name
        df.to_csv(p, index=write_index)
        written.append(p)
    return written


def _ensure_mpl():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise RuntimeError(
            "matplotlib is not installed. Install with: "
            'pip install -e ".[figures]" — or use `astralyzer analyze --no-figures`.'
        ) from e
    return plt


_IEA_LABELS = {0: "0 (none)", 1: "1 (partial)", 2: "2 (independent)"}


def _label_iea_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Replace IEA column codes with human labels for figure legends."""
    return df.rename(columns=lambda c: _IEA_LABELS.get(c, "unspecified" if pd.isna(c) else str(c)))


def write_figures(out_dir: Path) -> list[Path]:
    plt = _ensure_mpl()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    df = iea_by_instrument()
    if not df.empty:
        ax = _label_iea_columns(df).plot(
            kind="bar", stacked=True, figsize=(7, 4), edgecolor="white")
        ax.set_xlabel("Instrument type")
        ax.set_ylabel("Adjudicated codes")
        ax.set_title("Independent epistemic access by instrument type")
        ax.legend(title="IEA", loc="best")
        plt.tight_layout()
        p = out_dir / "iea_by_instrument.pdf"
        plt.savefig(p)
        plt.close()
        written.append(p)

    df = iea_by_domain()
    if not df.empty:
        ax = _label_iea_columns(df).plot(
            kind="bar", stacked=True, figsize=(7, 4), edgecolor="white")
        ax.set_xlabel("Coupling domain")
        ax.set_ylabel("Adjudicated codes (per domain membership)")
        ax.set_title("Independent epistemic access by coupling domain")
        ax.legend(title="IEA", loc="best")
        plt.tight_layout()
        p = out_dir / "iea_by_domain.pdf"
        plt.savefig(p)
        plt.close()
        written.append(p)

    ad = authority_default_distribution()
    if not ad.empty:
        ax = ad.set_index("authority_default")["count"].plot(
            kind="bar", figsize=(6, 4))
        ax.set_xlabel("authority_default")
        ax.set_ylabel("Adjudicated codes")
        ax.set_title("Authority default distribution")
        plt.tight_layout()
        p = out_dir / "authority_default.pdf"
        plt.savefig(p)
        plt.close()
        written.append(p)

    return written
