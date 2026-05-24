"""LLM suggestion layer (Phase 4).

All suggestions are written as status='suggested', attributed to an LLM coder.
The DB trigger trg_codes_llm_suggested_only_ins enforces this at the storage
level; this package is the engine that produces those rows. Suggestions are
NEVER promoted to human_draft or adjudicated by code — that requires a human
action in the review UI.
"""
