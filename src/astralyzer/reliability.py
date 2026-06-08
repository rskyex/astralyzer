"""Inter-coder reliability: sample selection, double-coding pairing, kappa.

Methodological constraint #3 (docs/methodology.md): reliability is computed
from INDEPENDENT human double-coding only. Enforced at three levels:

  1. The schema check `coder_a_id != coder_b_id`.
  2. The trigger `trg_reliability_humans_only` (migration 0002): inserts with
     a non-human coder on either side are aborted.
  3. The review UI hides each coder's drafts from the other while the run's
     status is 'open' (see review.repo.list_codes_for_provision).

Coverage of fields:
  * Per-field Cohen's kappa + percent agreement for controlled-vocab fields
    (operative_function, authority_default, verification_mechanism,
    independent_epistemic_access, term_id).
  * Per-domain binary kappa for coupling_domains (each of space/nuclear/
    cyber/ai treated as in/out).
  * Free-text fields (operative_term_raw, ai_operation_effect, rationale)
    are not reduced to kappa; they appear in the disagreements view for
    qualitative comparison.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass

from astralyzer.db import open_conn


CODABLE_VOCAB_FIELDS = [
    "operative_function",
    "authority_default",
    "verification_mechanism",
    "independent_epistemic_access",
    "term_id",
]
COUPLING_DOMAINS = ["space", "nuclear", "cyber", "ai"]


class ReliabilityError(Exception):
    pass


@dataclass
class ComputeResult:
    run_id: str
    n_pairs: int
    n_missing: int
    kappa: dict[str, float | None]
    agreement: dict[str, float | None]


# ---------------------------------------------------------------------------
# Sample selection and run creation
# ---------------------------------------------------------------------------

def select_sample(
    *,
    document_id: str | None = None,
    n: int = 10,
    seed: int | None = None,
) -> list[str]:
    """Return a deterministic random sample of provision ids."""
    with open_conn() as conn:
        if document_id is not None:
            rows = conn.execute(
                "SELECT id FROM provisions WHERE document_id = ? ORDER BY id",
                (document_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM provisions ORDER BY id"
            ).fetchall()
    pool = [r["id"] for r in rows]
    if not pool:
        raise ReliabilityError("no provisions available to sample from")
    if n >= len(pool):
        return pool
    rng = random.Random(seed)
    return sorted(rng.sample(pool, n))


def create_run(
    *,
    run_id: str,
    coder_a_id: str,
    coder_b_id: str,
    sample_provisions: list[str],
) -> None:
    """Create a reliability run, validate inputs, populate the membership
    join table. The DB trigger enforces both coders are human."""
    if coder_a_id == coder_b_id:
        raise ReliabilityError("coder_a and coder_b must be distinct")
    if not sample_provisions:
        raise ReliabilityError("sample is empty")

    with open_conn() as conn:
        # Validate provisions exist.
        for pid in sample_provisions:
            if conn.execute(
                "SELECT 1 FROM provisions WHERE id = ?", (pid,)
            ).fetchone() is None:
                raise ReliabilityError(f"no such provision: {pid!r}")

        conn.execute(
            "INSERT INTO reliability_runs"
            "(id, coder_a_id, coder_b_id, sample_provisions, status) "
            "VALUES (?, ?, ?, ?, 'open')",
            (run_id, coder_a_id, coder_b_id, json.dumps(sample_provisions)),
        )
        for pid in sample_provisions:
            conn.execute(
                "INSERT INTO reliability_run_provisions(run_id, provision_id) "
                "VALUES (?, ?)",
                (run_id, pid),
            )
        conn.commit()


def list_runs() -> list[dict]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, coder_a_id, coder_b_id, status, created_at "
            "FROM reliability_runs ORDER BY created_at DESC"
        )]


def get_run(run_id: str) -> dict | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reliability_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["sample_provisions"] = json.loads(d["sample_provisions"])
        for k in ("per_field_kappa", "percent_agreement"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        return d


# ---------------------------------------------------------------------------
# Blinding helpers (consumed by review.repo)
# ---------------------------------------------------------------------------

def coders_blinded_from(provision_id: str, current_coder_id: str) -> list[str]:
    """Coder ids whose codes should be hidden from current_coder for this
    provision because of an open reliability run."""
    with open_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT "
            "  CASE WHEN r.coder_a_id = :me THEN r.coder_b_id "
            "       ELSE r.coder_a_id END AS other "
            "FROM reliability_runs r "
            "JOIN reliability_run_provisions rp ON rp.run_id = r.id "
            "WHERE rp.provision_id = :pid "
            "  AND r.status = 'open' "
            "  AND (r.coder_a_id = :me OR r.coder_b_id = :me)",
            {"me": current_coder_id, "pid": provision_id},
        ).fetchall()
    return [r["other"] for r in rows]


def open_runs_for_provision(provision_id: str) -> list[dict]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT r.id, r.coder_a_id, r.coder_b_id "
            "FROM reliability_runs r "
            "JOIN reliability_run_provisions rp ON rp.run_id = r.id "
            "WHERE rp.provision_id = ? AND r.status = 'open'",
            (provision_id,)
        )]


# ---------------------------------------------------------------------------
# Cohen's kappa
# ---------------------------------------------------------------------------

def cohens_kappa(a: list, b: list) -> float | None:
    """Standard Cohen's kappa. None values are treated as a category so
    "both did not code this field" counts as agreement.

    Returns None for empty input or when expected agreement is 1.0 (which
    happens iff every annotator used a single category — kappa is undefined).
    """
    if len(a) != len(b):
        raise ValueError("length mismatch")
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    categories = set(a) | set(b)
    pe = 0.0
    for c in categories:
        pe += (a.count(c) / n) * (b.count(c) / n)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else None
    return (po - pe) / (1 - pe)


def percent_agreement(a: list, b: list) -> float | None:
    if len(a) != len(b) or not a:
        return None
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


# ---------------------------------------------------------------------------
# Pair collection and compute
# ---------------------------------------------------------------------------

def _latest_human_code(conn, provision_id: str, coder_id: str) -> dict | None:
    row = conn.execute(
        "SELECT c.*, "
        "  (SELECT json_group_array(domain) "
        "   FROM code_coupling_domains WHERE code_id = c.id) AS domains_json "
        "FROM codes c "
        "WHERE c.provision_id = ? AND c.coder_id = ? "
        "  AND c.status IN ('human_draft', 'adjudicated') "
        "ORDER BY c.created_at DESC LIMIT 1",
        (provision_id, coder_id),
    ).fetchone()
    return dict(row) if row else None


def _domains_set(code: dict) -> set[str]:
    raw = code.get("domains_json")
    if not raw:
        return set()
    return set(json.loads(raw))


def collect_pairs(run_id: str) -> tuple[list[dict], list[str]]:
    """Return (paired, missing). Each paired element has 'provision_id',
    'a' (latest A code dict), 'b' (latest B code dict)."""
    with open_conn() as conn:
        run = conn.execute(
            "SELECT * FROM reliability_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise ReliabilityError(f"no such run: {run_id!r}")
        sample = json.loads(run["sample_provisions"])
        paired = []
        missing = []
        for pid in sample:
            a = _latest_human_code(conn, pid, run["coder_a_id"])
            b = _latest_human_code(conn, pid, run["coder_b_id"])
            if a is None or b is None:
                missing.append(pid)
                continue
            paired.append({"provision_id": pid, "a": a, "b": b})
    return paired, missing


def _json_safe(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def compute_run(run_id: str) -> ComputeResult:
    paired, missing = collect_pairs(run_id)

    kappa: dict[str, float | None] = {}
    agreement: dict[str, float | None] = {}

    for field in CODABLE_VOCAB_FIELDS:
        la = [p["a"][field] for p in paired]
        lb = [p["b"][field] for p in paired]
        kappa[field] = cohens_kappa(la, lb)
        agreement[field] = percent_agreement(la, lb)

    for domain in COUPLING_DOMAINS:
        la = [1 if domain in _domains_set(p["a"]) else 0 for p in paired]
        lb = [1 if domain in _domains_set(p["b"]) else 0 for p in paired]
        key = f"coupling_domain:{domain}"
        kappa[key] = cohens_kappa(la, lb)
        agreement[key] = percent_agreement(la, lb)

    safe_kappa = {k: _json_safe(v) for k, v in kappa.items()}
    safe_agreement = {k: _json_safe(v) for k, v in agreement.items()}

    with open_conn() as conn:
        conn.execute(
            "UPDATE reliability_runs SET "
            "  per_field_kappa = ?, percent_agreement = ?, status = 'computed' "
            "WHERE id = ?",
            (json.dumps(safe_kappa), json.dumps(safe_agreement), run_id),
        )
        conn.commit()

    return ComputeResult(
        run_id=run_id,
        n_pairs=len(paired),
        n_missing=len(missing),
        kappa=safe_kappa,
        agreement=safe_agreement,
    )


# ---------------------------------------------------------------------------
# Disagreements (for the review UI)
# ---------------------------------------------------------------------------

def list_disagreements(run_id: str) -> list[dict]:
    """Return per-provision disagreement records. Each has provision_id and
    a list of (field, a_value, b_value) tuples for fields where the two
    coders' latest codes differ. Free-text fields are flagged separately."""
    paired, _missing = collect_pairs(run_id)
    out = []
    for p in paired:
        a, b = p["a"], p["b"]
        diffs = []
        for f in CODABLE_VOCAB_FIELDS:
            if a[f] != b[f]:
                diffs.append({"field": f, "a": a[f], "b": b[f]})
        a_dom = _domains_set(a)
        b_dom = _domains_set(b)
        if a_dom != b_dom:
            diffs.append({
                "field": "coupling_domains",
                "a": sorted(a_dom),
                "b": sorted(b_dom),
            })
        # Free-text fields: flag presence/inequality without trying to score.
        for f in ("operative_term_raw", "ai_operation_effect"):
            if (a[f] or "") != (b[f] or ""):
                diffs.append({"field": f, "a": a[f], "b": b[f]})
        if diffs:
            out.append({
                "provision_id": p["provision_id"],
                "a_code_id": a["id"],
                "b_code_id": b["id"],
                "diffs": diffs,
            })
    return out
