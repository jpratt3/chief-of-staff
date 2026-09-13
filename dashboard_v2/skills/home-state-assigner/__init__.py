"""Home State Assigner Blueprint"""
import os, uuid, tempfile
from flask import Blueprint, render_template, request, jsonify
from .service import assign_home_state
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
bp = Blueprint("home_state_assigner", __name__, template_folder="templates", url_prefix="/skills/home-state-assigner")

@bp.route("/", methods=["GET"])
def index():
    clients = [c["display_name"] for c in _get_clients()]
    return render_template("home_state_assigner/index.html", clients=clients)

@bp.route("/assign", methods=["POST"])
def assign():
    f = request.files.get("policy_file")
    client_name = request.form.get("client_name","").strip()
    prior_state = request.form.get("prior_state","").strip()
    if not f or not client_name:
        return jsonify({"ok": False, "error": "File and client required."})
    tmp = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}_{f.filename}")
    f.save(tmp)
    result = assign_home_state(tmp, client_name, prior_state)
    if not result.success:
        return jsonify({"ok": False, "error": result.error})
    return jsonify({"ok": True, "detected_state": result.detected_state, "confidence": result.confidence, "email_draft": result.email_draft})
