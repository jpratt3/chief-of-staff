"""Header Updates Blueprint"""
from flask import Blueprint, render_template, request, jsonify
from .service import build_header_updates
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
bp = Blueprint("header_updates", __name__, template_folder="templates", url_prefix="/skills/header-updates")
LINES = ["General Liability","Property","Auto","Workers Compensation","Umbrella / Excess","Cyber","D&O / Management Liability","Crime","EPLI"]

@bp.route("/", methods=["GET"])
def index():
    clients = [c["display_name"] for c in _get_clients()]
    return render_template("header_updates/index.html", clients=clients, lines=LINES)

@bp.route("/build", methods=["POST"])
def build():
    body = request.get_json(force=True)
    client_name = body.get("client_name","").strip()
    lines = body.get("lines",[])
    year = body.get("renewal_year","").strip()
    result = build_header_updates(client_name, lines, year)
    if not result.success:
        return jsonify({"ok": False, "error": result.error})
    return jsonify({"ok": True, "ams_steps": result.ams_steps, "note": result.confirmation_note})
