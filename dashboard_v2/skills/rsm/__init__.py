"""
RSM Deck Builder — Deck Builder · RSM #2

Routes:
  GET  /skills/rsm           upload form + market-slide picker
  POST /skills/rsm/build     assemble the deck, return a JSON preview
  GET  /skills/rsm/download  stream the assembled .pptx
"""
from __future__ import annotations

import hashlib
import io
import time

from flask import Blueprint, jsonify, render_template, request, send_file

from .service import build_rsm, market_db_by_category, safe_filename

bp = Blueprint(
    "rsm",
    __name__,
    template_folder="templates",
    url_prefix="/skills/rsm",
)

_PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

# Built decks are held in memory until downloaded. Single-user, local-only tool
# (see dashboard_v2/README.md) — a dict is the right amount of machinery.
_PENDING: dict[str, bytes] = {}


@bp.get("/")
def index():
    return render_template("rsm/index.html", categories=market_db_by_category())


@bp.post("/build")
def build():
    old_deck = request.files.get("old_deck")
    pg_file = request.files.get("program_graphic")
    client_name = request.form.get("client_name", "").strip()
    new_year = request.form.get("new_year", "").strip()
    old_year = request.form.get("old_year", "").strip()
    selected_ids = request.form.getlist("market_slides")

    errors = []
    if not old_deck or not old_deck.filename:
        errors.append("Please upload last year's RSM deck (.pptx).")
    elif not old_deck.filename.lower().endswith(".pptx"):
        errors.append("RSM deck must be a .pptx file.")
    if not client_name:
        errors.append("Client name is required.")
    if not new_year:
        errors.append("New policy year is required (e.g. 2026-27).")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    result = build_rsm(
        old_deck_bytes=old_deck.read(),
        program_graphic_bytes=(pg_file.read() if pg_file and pg_file.filename else None),
        client_name=client_name,
        new_year=new_year,
        old_year=old_year,
        selected_market_ids=selected_ids,
    )
    if not result.success:
        return jsonify({"ok": False, "errors": [result.error]}), 500

    key = hashlib.md5(f"{client_name}{time.time()}".encode()).hexdigest()[:12]
    _PENDING[key] = result.data["pptx_bytes"]

    return jsonify({
        "ok": True,
        "download_key": key,
        "filename": safe_filename(client_name, new_year),
        "slide_count": result.data["slide_count"],
        "slide_log": result.data["slide_log"],
    })


@bp.get("/download")
def download():
    pptx_bytes = _PENDING.pop(request.args.get("key", ""), None)
    if not pptx_bytes:
        return "Deck not found or already downloaded.", 404
    return send_file(
        io.BytesIO(pptx_bytes),
        as_attachment=True,
        download_name=request.args.get("filename", "rsm_deck.pptx"),
        mimetype=_PPTX_MIME,
    )
