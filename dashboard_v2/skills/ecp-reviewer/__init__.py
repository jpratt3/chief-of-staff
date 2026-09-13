"""ECP Reviewer Blueprint"""
import os, uuid, tempfile
from flask import Blueprint, render_template, request, jsonify
from .service import review_ecp
def _get_clients():
    import importlib.util, sys
    from pathlib import Path
    dl = Path(__file__).parent.parent.parent / "data_loader.py"
    if "dashboard_v2.data_loader" in sys.modules:
        return sys.modules["dashboard_v2.data_loader"].load_clients()
    spec = importlib.util.spec_from_file_location("_v2_dl", dl)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_clients()
bp = Blueprint("ecp_reviewer", __name__, template_folder="templates", url_prefix="/skills/ecp-reviewer")

@bp.route("/", methods=["GET"])
def index():
    clients = [c["display_name"] for c in _get_clients()]
    return render_template("ecp_reviewer/index.html", clients=clients)

@bp.route("/review", methods=["POST"])
def review():
    f = request.files.get("ecp_file")
    client_name = request.form.get("client_name","").strip()
    if not f or not client_name:
        return jsonify({"ok": False, "error": "File and client required."})
    tmp = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}_{f.filename}")
    f.save(tmp)
    result = review_ecp(tmp, client_name)
    if not result.success:
        return jsonify({"ok": False, "error": result.error})
    return jsonify({"ok": True, "surplus_confirmed": result.surplus_confirmed, "extracted_fields": result.extracted_fields, "email_bullet": result.email_bullet, "carrier": result.carrier, "state": result.state})
