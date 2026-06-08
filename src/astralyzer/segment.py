"""Segment a document into provisions at article/section granularity.

Two paths:
- segment_default: best-effort regex over common markers (Article I/1, Art. 1,
  Section 1, § 1). If no markers are found, returns a single "(whole)" segment.
- segment_from_anchors: manual override. Caller supplies a list of
  {'anchor': str, 'char_start': int}; end offsets are inferred from the next
  anchor (or document end). Strictly increasing char_start values; bounds checked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    citation_anchor: str
    char_start: int
    char_end: int
    text: str


_PATTERNS = [
    r"Article\s+[IVXLCDM]+\.?",
    r"Article\s+\d+(?:\.\d+)*\.?",
    r"Art\.\s+[IVXLCDM]+\.?",
    r"Art\.\s+\d+(?:\.\d+)*\.?",
    r"Section\s+\d+(?:\.\d+)*\.?",
    r"§\s*\d+(?:\.\d+)*\.?",
]
_COMBINED = re.compile(r"^(?:" + "|".join(_PATTERNS) + r")", re.MULTILINE)


def _normalize_anchor(s: str) -> str:
    return s.strip().rstrip(".").strip()


def segment_default(text: str) -> list[Segment]:
    matches = [(m.start(), m.group()) for m in _COMBINED.finditer(text)]
    if not matches:
        return [Segment("(whole)", 0, len(text), text)]

    segments: list[Segment] = []
    # Preserve any non-whitespace prose before the first marker as a "Preamble"
    # segment. Treaty preambles routinely carry operative language; dropping
    # them silently would lose coding material.
    first_start = matches[0][0]
    if text[:first_start].strip():
        pre_text = text[:first_start].rstrip()
        segments.append(Segment("Preamble", 0, len(pre_text), pre_text))

    for i, (start, anchor) in enumerate(matches):
        prov_end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
        prov_text = text[start:prov_end].rstrip()
        actual_end = start + len(prov_text)
        segments.append(Segment(_normalize_anchor(anchor), start, actual_end, prov_text))
    return segments


def segment_from_anchors(text: str, anchors: list[dict]) -> list[Segment]:
    if not anchors:
        raise ValueError("anchors list is empty")
    for i, a in enumerate(anchors):
        if "anchor" not in a or "char_start" not in a:
            raise ValueError(f"each anchor needs 'anchor' and 'char_start': got {a!r}")
        start = a["char_start"]
        if not isinstance(start, int) or start < 0 or start > len(text):
            raise ValueError(
                f"char_start {start!r} out of bounds [0, {len(text)}] for {a['anchor']!r}"
            )
        if i > 0 and start <= anchors[i - 1]["char_start"]:
            raise ValueError(
                "char_start values must be strictly increasing; "
                f"{a['anchor']!r} ({start}) follows {anchors[i-1]['anchor']!r} "
                f"({anchors[i-1]['char_start']})"
            )

    segments: list[Segment] = []
    for i, a in enumerate(anchors):
        start = a["char_start"]
        end = anchors[i + 1]["char_start"] if i + 1 < len(anchors) else len(text)
        seg_text = text[start:end].rstrip()
        actual_end = start + len(seg_text)
        segments.append(Segment(_normalize_anchor(a["anchor"]), start, actual_end, seg_text))
    return segments
