"""Internal Strategy Meeting (ISM) Deck Builder Blueprint"""
import os, uuid
from flask import Blueprint, render_template, request, jsonify, send_file
from .service import build_strategy_deck

bp = Blueprint("strategy_deck", __name__, template_folder="templates", url_prefix="/skills/strategy-deck")
_BUILT = {}

@bp.route("/", methods=["GET"])
def index():
    return render_template("strategy_deck/index.html")

@bp.route("/build", methods=["POST"])
def build():
    f = request.files.get("deck")
    client_name = request.form.get("client_name","").strip()
    new_year = request.form.get("new_year","").strip()
    old_year = request.form.get("old_year","").strip()
    if not f or not client_name or not new_year:
        return jsonify({"ok": False, "error": "Deck, client, and year required."})
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}_{f.filename}")
    f.save(tmp)
    result = build_strategy_deck(tmp, client_name, new_year, old_year)
    if not result.success:
        return jsonify({"ok": False, "error": result.error})
    key = str(uuid.uuid4())
    _BUILT[key] = result.output_path
    return jsonify({"ok": True, "key": key, "filename": result.filename, "slides": result.slide_count})

@bp.route("/download")
def download():
    key = request.args.get("key","")
    path = _BUILT.get(key)
    if not path or not os.path.exists(path):
        return "Not found", 404
    return send_file(path, as_attachment=True, download_name=request.args.get("filename","ism_deck.pptx"))
