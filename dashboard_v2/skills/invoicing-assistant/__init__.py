"""Invoicing Assistant Blueprint — Invoice stage, tasks #1-#3."""
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify, Response

from .service import (
    ALLOWED_EXTENSIONS, billing_ids, build_csv, build_summary_text,
    client_options, extract_rows, summarize,
)

bp = Blueprint("invoicing_assistant", __name__, template_folder="templates",
               url_prefix="/skills/invoicing-assistant")


def _accepted(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in ALLOWED_EXTENSIONS


@bp.route("/", methods=["GET"])
def index():
    return render_template("invoicing_assistant/index.html", clients=client_options())


@bp.route("/upload-batch", methods=["POST"])
def upload_batch():
    """Extract premium, commission and charges from N binders."""
    files = request.files.getlist("binders")
    if not files:
        return jsonify({"ok": False, "error": "No files uploaded."})

    batch, rejected = [], []
    for f in files:
        if not f.filename:
            continue
        if _accepted(f.filename):
            batch.append((f.read(), f.filename))
        else:
            rejected.append(f.filename)

    if not batch:
        return jsonify({
            "ok": False,
            "error": "No supported files. Binders must be PDF."
                     + (f" Rejected: {', '.join(rejected)}." if rejected else ""),
        })

    rows = extract_rows(batch)
    detected = next((r["client_key"] for r in rows if r.get("client_key")), "")
    return jsonify({
        "ok": True,
        "rows": rows,
        "summary": summarize(rows),
        "client": billing_ids(client_key=detected),
        "rejected": rejected,
    })


@bp.route("/client", methods=["POST"])
def client_lookup():
    """CN and billing ID for the client the user picked."""
    body = request.get_json(force=True, silent=True) or {}
    return jsonify({"ok": True, "client": billing_ids(
        client_key=body.get("client_key", ""),
        display_name=body.get("display_name", ""),
        billing_id=body.get("billing_id", ""),
    )})


@bp.route("/recap", methods=["POST"])
def recap():
    """Re-total the edited table and return the paste-ready summary."""
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("rows", [])
    if not rows:
        return jsonify({"ok": False, "error": "No rows to summarise."})
    client = billing_ids(client_key=body.get("client_key", ""),
                         display_name=body.get("display_name", ""),
                         billing_id=body.get("billing_id", ""))
    return jsonify({"ok": True, "summary": summarize(rows), "client": client,
                    "text": build_summary_text(rows, client)})


@bp.route("/export", methods=["POST"])
def export():
    """CSV of every line, for the invoice request backup."""
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("rows", [])
    if not rows:
        return jsonify({"ok": False, "error": "No rows to export."})
    client = billing_ids(client_key=body.get("client_key", ""),
                         display_name=body.get("display_name", ""),
                         billing_id=body.get("billing_id", ""))
    name = (client.get("client") or "placement").replace(" ", "_")
    return Response(
        build_csv(rows, client),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}_invoicing.csv"'},
    )
