"""Renewal Kickoff Blueprint"""
from flask import Blueprint, render_template, request, jsonify
from .service import build_kickoff_email
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
bp = Blueprint("renewal_kickoff", __name__, template_folder="templates", url_prefix="/skills/renewal-kickoff")

@bp.route("/", methods=["GET"])
def index():
    clients = [c["display_name"] for c in _get_clients()]
    return render_template("renewal_kickoff/index.html", clients=clients)

@bp.route("/build", methods=["POST"])
def build():
    body = request.get_json(force=True)
    client_name = body.get("client_name","").strip()
    if not client_name:
        return jsonify({"ok": False, "error": "No client selected."})
    result = build_kickoff_email(client_name)
    if not result.success:
        return jsonify({"ok": False, "error": result.error})
    return jsonify({"ok": True, "email_draft": result.email_draft, "team_members": result.team_members, "renewal_date": result.renewal_date})
