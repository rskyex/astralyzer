"""Document ingestion.

Idempotent on identical text (same document_id + same sha256 → no-op). Refuses
on sha mismatch: if a document with the given id already exists but the text
changed, the user must use a new id (the safe path) or delete the existing
document explicitly (which cascades to provisions and codes).

The canonical on-disk location for raw text is data/raw/<document_id>.txt,
written relative to the repo root. raw_text_sha256 is the hash of the in-memory
text (UTF-8), not of the file as written.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from astralyzer import db as db_mod
from astralyzer.db import open_conn
from astralyzer.ids import provision_id
from astralyzer.segment import Segment, segment_default, segment_from_anchors


class IngestError(Exception):
    pass


@dataclass
class IngestResult:
    document_id: str
    sha256: str
    n_provisions: int
    status: str  # "created" | "unchanged"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _raw_text_path(document_id: str) -> Path:
    return db_mod.ROOT / "data" / "raw" / f"{document_id}.txt"


def _rel_raw_ref(document_id: str) -> str:
    return f"data/raw/{document_id}.txt"


def ingest_document(
    *,
    document_id: str,
    short_name: str,
    full_title: str,
    instrument_type: str,
    official_source_url: str,
    version_or_date: str,
    retrieval_date: str,
    text: str,
    segments_override: list[dict] | None = None,
    single_provision: bool = False,
    notes: str | None = None,
) -> IngestResult:
    if segments_override is not None and single_provision:
        raise IngestError("segments_override and single_provision are mutually exclusive")

    sha = _sha256_text(text)

    with open_conn() as conn:
        existing = conn.execute(
            "SELECT raw_text_sha256 FROM documents WHERE id = ?", (document_id,)
        ).fetchone()

        if existing is not None:
            if existing["raw_text_sha256"] == sha:
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM provisions WHERE document_id = ?",
                    (document_id,),
                ).fetchone()["n"]
                return IngestResult(document_id, sha, n, "unchanged")
            raise IngestError(
                f"document '{document_id}' already exists with a different sha256.\n"
                f"  existing: {existing['raw_text_sha256']}\n"
                f"  incoming: {sha}\n"
                "Use a new document id (e.g. add a version suffix), or delete the "
                "existing document explicitly — deletion cascades to provisions and codes."
            )

        if single_provision:
            segments: list[Segment] = [Segment("(whole)", 0, len(text), text)]
        elif segments_override is not None:
            segments = segment_from_anchors(text, segments_override)
        else:
            segments = segment_default(text)

        raw_path = _raw_text_path(document_id)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(text, encoding="utf-8")

        conn.execute(
            "INSERT INTO documents("
            "id, short_name, full_title, instrument_type, official_source_url, "
            "version_or_date, retrieval_date, raw_text_ref, raw_text_sha256, notes) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document_id, short_name, full_title, instrument_type,
                official_source_url, version_or_date, retrieval_date,
                _rel_raw_ref(document_id), sha, notes,
            ),
        )
        for i, seg in enumerate(segments, start=1):
            conn.execute(
                "INSERT INTO provisions("
                "id, document_id, ordinal, citation_anchor, text, char_start, char_end) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    provision_id(document_id, i), document_id, i,
                    seg.citation_anchor, seg.text, seg.char_start, seg.char_end,
                ),
            )
        conn.commit()
        return IngestResult(document_id, sha, len(segments), "created")


def list_documents() -> list[dict]:
    with open_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, short_name, instrument_type, version_or_date, "
            "retrieval_date, raw_text_sha256 FROM documents ORDER BY id"
        )]


def show_document(document_id: str) -> dict | None:
    with open_conn() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if doc is None:
            return None
        provisions = [dict(r) for r in conn.execute(
            "SELECT id, ordinal, citation_anchor, char_start, char_end, text "
            "FROM provisions WHERE document_id = ? ORDER BY ordinal",
            (document_id,)
        )]
        return {"document": dict(doc), "provisions": provisions}


def load_segments_yaml(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    segments = data.get("segments") if isinstance(data, dict) else None
    if not segments:
        raise IngestError(f"{path} has no 'segments' list at the top level")
    out: list[dict] = []
    for s in segments:
        if "anchor" not in s or "char_start" not in s:
            raise IngestError(
                f"each segment requires 'anchor' and 'char_start': got {s!r}"
            )
        out.append({"anchor": str(s["anchor"]), "char_start": int(s["char_start"])})
    return out
