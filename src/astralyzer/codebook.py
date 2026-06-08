"""Load and read the living codebook.

The on-disk source of truth is `codebook/codebook.yaml`. Loading upserts into
the `codebook_fields` table, bumping `version` and `updated_at` per row.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from astralyzer.db import open_conn


def load_from_yaml(path: Path) -> int:
    """Upsert all fields from the given YAML. Returns the number of fields written."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    version = data["version"]
    fields = data["fields"]
    n = 0
    with open_conn() as conn:
        for f in fields:
            conn.execute(
                """
                INSERT INTO codebook_fields
                    (field, definition, decision_rules, examples, value_domain, version)
                VALUES
                    (:field, :definition, :decision_rules, :examples, :value_domain, :version)
                ON CONFLICT(field) DO UPDATE SET
                    definition     = excluded.definition,
                    decision_rules = excluded.decision_rules,
                    examples       = excluded.examples,
                    value_domain   = excluded.value_domain,
                    version        = excluded.version,
                    updated_at     = datetime('now')
                """,
                {
                    "field": f["field"],
                    "definition": f["definition"],
                    "decision_rules": f["decision_rules"],
                    "examples": json.dumps(f.get("examples") or []),
                    "value_domain": (json.dumps(f["value_domain"])
                                     if f.get("value_domain") is not None else None),
                    "version": version,
                },
            )
            n += 1
        conn.commit()
    return n


def list_fields() -> list[dict[str, Any]]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT field, version, updated_at FROM codebook_fields ORDER BY field")]


def get_field(field: str) -> dict[str, Any] | None:
    with open_conn() as conn:
        row = conn.execute(
            "SELECT * FROM codebook_fields WHERE field = ?", (field,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["examples"] = json.loads(d["examples"]) if d["examples"] else []
    if d["value_domain"]:
        d["value_domain"] = json.loads(d["value_domain"])
    return d
