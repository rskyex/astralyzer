"""Stable ID helpers."""
from __future__ import annotations

import re


def slugify(text: str) -> str:
    """Lowercase + non-alphanumeric → hyphens. For document and term IDs."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def provision_id(document_id: str, ordinal: int) -> str:
    """Deterministic provision ID. Stable across re-ingestion of identical text."""
    return f"{document_id}:{ordinal}"
