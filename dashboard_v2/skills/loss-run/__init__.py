"""Loss Run Request Blueprint"""
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify

from .service import extract_rows, build_drafts, ALLOWED_EXTENSIONS


def _get_clients():
    import importlib.util, sys
    dl = Path(__file__).parent.parent.parent / "data_loader.py"
    if "dashboard_v2.data_loader" in sys.modules:
        return sys.modules["dashboard_v2.data_loader"].load_clients()
    spec = importlib.util.spec_from_file_location("_v2_dl", dl)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_clients()


bp = Blueprint("loss_run", __name__, template_folder="templates",
               url_prefix="/skills/loss-run")


def _accepted(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in ALLOWED_EXTENSIONS


@bp.route("/", methods=["GET"])
def index():
    clients = [c["display_name"] for c in _get_clients()]
    return render_template("loss_run/index.html", clients=clients)


@bp.route("/upload-batch", methods=["POST"])
def upload_batch():
    """Extract fields from N binders. Unsupported files are reported back
    rather than failing the batch."""
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
            "error": "No supported files. Accepted: PDF, DOCX, XLSX, XLS, CSV."
                     + (f" Rejected: {', '.join(rejected)}." if rejected else ""),
        })

    return jsonify({"ok": True, "rows": extract_rows(batch),
                    "rejected": rejected, "count": len(batch)})


@bp.route("/rescan", methods=["POST"])
def rescan():
    """Re-extract one file with no page cap, for low-confidence rows."""
    f = request.files.get("binder")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file uploaded."})
    if not _accepted(f.filename):
        return jsonify({"ok": False, "error": f"Unsupported file type: {f.filename}"})

    rows = extract_rows([(f.read(), f.filename)], full_scan=True)
    if not rows:
        return jsonify({"ok": False, "error": "Extraction returned no result."})
    return jsonify({"ok": True, "row": rows[0]})


@bp.route("/draft-batch", methods=["POST"])
def draft_batch():
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("rows", [])
    if not rows:
        return jsonify({"ok": False, "error": "No rows to draft."})

    result = build_drafts(
        rows=rows,
        requestor_name=body.get("requestor_name", "").strip(),
        requestor_email=body.get("requestor_email", "").strip(),
        years=int(body.get("years", 5)),
    )
    if not result.success:
        return jsonify({"ok": False, "error": result.error})
    return jsonify({"ok": True, **result.data})
