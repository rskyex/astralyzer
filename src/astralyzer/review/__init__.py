"""Flask review UI for the Interpretive Authority Mapping Engine.

Phase 3 scope: view a document and its provisions side by side, see existing
codes (suggestions, drafts, adjudicated) on a provision, create/edit/delete
human drafts, promote a draft to adjudicated. Local, single-user. The coder
identity is held in a plain (unsigned) cookie — there is no auth layer; this
is a local-first dev tool, not a deployment surface.

Constraints enforced here that go beyond the schema:
  * Writes require a coder cookie set to a HUMAN coder. LLM coders are still
    selectable for browsing but rejected for writes at the app layer (the DB
    trigger would also reject them — this just gives a nicer error).
  * "Adjudicate" is a separate explicit action; it never happens silently.
"""
from __future__ import annotations
