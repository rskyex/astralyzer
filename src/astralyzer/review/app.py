"""Flask routes for the review UI.

Routes:
  GET  /                                  list documents
  POST /set-coder                         set current coder via cookie
  GET  /documents/<doc_id>                list provisions in a document
  GET  /provisions/<provision_id>         provision view with codes + draft form
  POST /provisions/<provision_id>/codes   create a human_draft code
  POST /codes/<code_id>                   update a code (must be own draft)
  POST /codes/<code_id>/adjudicate        promote draft to adjudicated
  POST /codes/<code_id>/delete            delete a draft or suggestion
  POST /suggestions/<code_id>/accept      create a human_draft from a suggestion

New canonical terms are created inline as part of code create/update, when the
form's `term_new` field is filled — no separate /terms route.
"""
from __future__ import annotations

import sqlite3
import uuid

from flask import Flask, abort, flash, redirect, render_template, request, url_for

from astralyzer.ids import slugify
from astralyzer.review import repo
from astralyzer.review.repo import VOCABS


def create_app() -> Flask:
    app = Flask(__name__,
                template_folder="templates",
                static_folder="static")
    app.secret_key = "astralyzer-local-dev"  # only used for flash messages

    @app.context_processor
    def inject_globals():
        handle = request.cookies.get("coder")
        coder = repo.get_coder(handle) if handle else None
        return {
            "current_coder": coder,
            "all_coders": repo.list_coders(),
            "VOCABS": VOCABS,
        }

    # ---------- helpers ----------------------------------------------------

    def _require_human_coder() -> dict:
        handle = request.cookies.get("coder")
        if not handle:
            abort(403, "select a coder before writing")
        c = repo.get_coder(handle)
        if c is None:
            abort(403, "unknown coder")
        if not c["is_human"]:
            abort(403, "LLM coders cannot write human drafts or adjudicate")
        return c

    def _extract_code_fields(form) -> dict:
        iea = form.get("independent_epistemic_access", "").strip()
        return {
            "operative_term_raw": form.get("operative_term_raw", "").strip() or None,
            "operative_function": form.get("operative_function", "").strip() or None,
            "authority_default": form.get("authority_default", "").strip() or None,
            "verification_mechanism": form.get("verification_mechanism", "").strip() or None,
            "independent_epistemic_access": int(iea) if iea else None,
            "ai_operation_effect": form.get("ai_operation_effect", "").strip() or None,
            "source_span_ref": form.get("source_span_ref", "").strip() or "(see provision text)",
            "rationale": form.get("rationale", "").strip() or None,
            "coupling_domains": form.getlist("coupling_domains"),
        }

    def _resolve_term(form, coder_id: str) -> str | None:
        """Return term_id from form: either an existing selection, a newly
        created term (when 'term_new' is filled), or None."""
        new_label = form.get("term_new", "").strip()
        if new_label:
            existing = repo.find_term_by_label(new_label)
            if existing:
                return existing["id"]
            term_id = slugify(new_label) or uuid.uuid4().hex[:8]
            try:
                repo.create_term(term_id, new_label, created_by=coder_id)
            except sqlite3.IntegrityError:
                # Lost a race or slug collision; just look it up.
                existing = repo.find_term_by_label(new_label)
                if existing:
                    return existing["id"]
                raise
            return term_id
        term_id = form.get("term_id", "").strip()
        return term_id or None

    # ---------- routes -----------------------------------------------------

    @app.route("/")
    def index():
        return render_template("index.html", documents=repo.list_documents())

    @app.route("/set-coder", methods=["POST"])
    def set_coder():
        handle = request.form.get("coder", "").strip()
        nxt = request.form.get("next") or url_for("index")
        resp = redirect(nxt, code=303)
        if handle:
            resp.set_cookie("coder", handle, max_age=60 * 60 * 24 * 30, samesite="Lax")
        else:
            resp.delete_cookie("coder")
        return resp

    @app.route("/documents/<doc_id>")
    def document_view(doc_id: str):
        doc = repo.get_document(doc_id)
        if doc is None:
            abort(404)
        return render_template(
            "document.html",
            document=doc,
            provisions=repo.list_provisions(doc_id),
        )

    @app.route("/provisions/<provision_id>")
    def provision_view(provision_id: str):
        prov = repo.get_provision(provision_id)
        if prov is None:
            abort(404)
        doc = repo.get_document(prov["document_id"])
        return render_template(
            "provision.html",
            provision=prov,
            document=doc,
            codes=repo.list_codes_for_provision(provision_id),
            terms=repo.list_terms(),
            neighbors=repo.get_provision_neighbors(provision_id),
        )

    @app.route("/provisions/<provision_id>/codes", methods=["POST"])
    def create_code(provision_id: str):
        coder = _require_human_coder()
        if repo.get_provision(provision_id) is None:
            abort(404)
        fields = _extract_code_fields(request.form)
        term_id = _resolve_term(request.form, coder["id"])
        code_id = uuid.uuid4().hex[:16]
        try:
            repo.create_code(
                code_id=code_id,
                provision_id=provision_id,
                coder_id=coder["id"],
                status="human_draft",
                term_id=term_id,
                **fields,
            )
        except sqlite3.IntegrityError as e:
            flash(f"could not create code: {e}", "error")
        return redirect(url_for("provision_view", provision_id=provision_id), code=303)

    @app.route("/codes/<code_id>", methods=["POST"])
    def update_code(code_id: str):
        coder = _require_human_coder()
        code = repo.get_code(code_id)
        if code is None:
            abort(404)
        if code["coder_id"] != coder["id"]:
            abort(403, "you may only edit codes you authored")
        if code["status"] == "adjudicated":
            abort(403, "adjudicated codes are immutable from the UI")
        fields = _extract_code_fields(request.form)
        term_id = _resolve_term(request.form, coder["id"])
        try:
            repo.update_code(code_id, term_id=term_id, **fields)
        except sqlite3.IntegrityError as e:
            flash(f"could not update code: {e}", "error")
        return redirect(url_for("provision_view", provision_id=code["provision_id"]), code=303)

    @app.route("/codes/<code_id>/adjudicate", methods=["POST"])
    def adjudicate_code(code_id: str):
        coder = _require_human_coder()
        code = repo.get_code(code_id)
        if code is None:
            abort(404)
        if code["status"] != "human_draft":
            abort(400, "only human drafts can be adjudicated")
        try:
            repo.adjudicate_code(code_id)
        except sqlite3.IntegrityError as e:
            flash(str(e), "error")
        return redirect(url_for("provision_view", provision_id=code["provision_id"]), code=303)

    @app.route("/codes/<code_id>/delete", methods=["POST"])
    def delete_code(code_id: str):
        _require_human_coder()
        code = repo.get_code(code_id)
        if code is None:
            abort(404)
        if code["status"] == "adjudicated":
            abort(403, "adjudicated codes cannot be deleted from the UI")
        provision_id = code["provision_id"]
        repo.delete_code(code_id)
        return redirect(url_for("provision_view", provision_id=provision_id), code=303)

    @app.route("/suggestions/<code_id>/accept", methods=["POST"])
    def accept_suggestion(code_id: str):
        """Create a NEW human_draft pre-filled from the suggestion. The
        original suggestion row is preserved (provenance: we keep the LLM
        output as it was)."""
        coder = _require_human_coder()
        sug = repo.get_code(code_id)
        if sug is None:
            abort(404)
        if sug["status"] != "suggested":
            abort(400, "not a suggestion")
        new_id = uuid.uuid4().hex[:16]
        repo.create_code(
            code_id=new_id,
            provision_id=sug["provision_id"],
            coder_id=coder["id"],
            status="human_draft",
            operative_term_raw=sug["operative_term_raw"],
            term_id=sug["term_id"],
            operative_function=sug["operative_function"],
            authority_default=sug["authority_default"],
            verification_mechanism=sug["verification_mechanism"],
            independent_epistemic_access=sug["independent_epistemic_access"],
            ai_operation_effect=sug["ai_operation_effect"],
            source_span_ref=sug["source_span_ref"],
            rationale=f"(accepted from suggestion by {sug['coder_id']}) {sug['rationale'] or ''}".strip(),
            coupling_domains=sug["coupling_domains"],
        )
        return redirect(url_for("provision_view", provision_id=sug["provision_id"]), code=303)

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message=str(e.description)), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404,
                               message=str(e.description) or "not found"), 404

    return app
