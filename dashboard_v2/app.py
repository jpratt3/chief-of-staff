"""
dashboard_v2/app.py
Flask entry point for CoS Dashboard v2 — port 8000.
Dual navigation: by stage (row) and by portal (column).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, Response

_V2_DIR = Path(__file__).parent
_ROOT   = _V2_DIR.parent

# Ensure dashboard_v2 is on sys.path for local imports
if str(_V2_DIR) not in sys.path:
    sys.path.insert(0, str(_V2_DIR))

# ...and the repo root, so `engine` (the shared extraction package) imports
# normally rather than through a file-path loader.
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_dotenv() -> None:
    """Read KEY=VALUE pairs from a local .env into the environment.

    Deliberately dependency-free and non-overriding: anything already exported
    in the shell wins, so a real environment is never clobbered by a stale file.
    .env is gitignored; .env.example documents the keys.
    """
    path = _ROOT / ".env"
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_dotenv()

from data_loader import (
    STAGES, PORTALS, PORTAL_META,
    get_overview_data, get_stage_detail, get_portal_data,
    toggle_completion, record_stage_response, load_skill_map,
)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024
app.config["SECRET_KEY"] = "cos-v2-dev"

STAGE_THEME = {
    "Renewal Preparation": {"color": "#5f78ff"},
    "RSM": {"color": "#7a8dff"},
    "Submission": {"color": "#8f9ef6"},
    "Proposal": {"color": "#a47ef5"},
    "Bind": {"color": "#d28a5c"},
    "Invoice": {"color": "#c88921"},
    "Post Binding": {"color": "#2d8b57"},
}

# ── Jinja globals ─────────────────────────────────────────────────────────────

def stage_to_slug(s: str) -> str:
    return s.lower().replace(" ", "-").replace("&", "and")

def portal_to_slug(p: str) -> str:
    return p.lower().replace(" ", "-")

def slug_to_stage(slug: str):
    for s in STAGES:
        if stage_to_slug(s) == slug:
            return s
    return None

def slug_to_portal(slug: str):
    for p in PORTALS:
        if portal_to_slug(p) == slug:
            return p
    return None

app.jinja_env.globals.update(
    stage_to_slug=stage_to_slug,
    portal_to_slug=portal_to_slug,
    STAGES=STAGES,
    PORTALS=PORTALS,
    PORTAL_META=PORTAL_META,
    STAGE_THEME=STAGE_THEME,
    SKILL_MAP=load_skill_map(),
)


# ── Renewal Preparation Skills ─────────────────────────────────────────────
import importlib as _il, sys as _sys

def _load_skill(dir_name: str, bp_var: str = "bp"):
    """Load a skill Blueprint from a hyphenated directory name."""
    mod_path = _V2_DIR / "skills" / dir_name / "__init__.py"
    mod_name = f"_skill_{dir_name.replace('-', '_')}"
    spec = _il.util.spec_from_file_location(mod_name, mod_path)
    mod = _il.util.module_from_spec(spec)
    _sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, bp_var)

for _skill_dir in [
    "exposure-request", "strategy-deck", "ecp-reviewer",
    "home-state-assigner", "renewal-kickoff",
    "header-updates", "team-confirmer", "strategy-scheduler",
    "loss-run", "invoicing-assistant", "rsm",
]:
    try:
        app.register_blueprint(_load_skill(_skill_dir))
    except Exception as _e:
        print(f"[skills] Could not load {_skill_dir}: {_e}")

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def overview():
    rows = get_overview_data()
    return render_template("overview.html", rows=rows, active_page="overview")


@app.route("/stage/<slug>")
def stage(slug: str):
    stage_name = slug_to_stage(slug)
    if not stage_name:
        return redirect(url_for("overview"))
    data = get_stage_detail(stage_name)
    return render_template("stage.html", data=data, active_page=f"stage-{slug}", stage_slug=slug)


@app.route("/portal/<slug>")
def portal(slug: str):
    portal_name = slug_to_portal(slug)
    if not portal_name:
        return redirect(url_for("overview"))
    data = get_portal_data(portal_name)
    return render_template("portal.html", data=data, active_page=f"portal-{slug}", portal_slug=slug)


@app.route("/matrix")
def matrix():
    """Full portal × stage matrix view."""
    from data_loader import PORTAL_SKILLS, load_clients, load_stage_responses, get_current_stage
    clients = load_clients()
    stage_responses = load_stage_responses()
    # Count clients per stage
    stage_counts = {s: 0 for s in STAGES}
    for c in clients:
        cs = get_current_stage(c, stage_responses)
        if cs in stage_counts:
            stage_counts[cs] += 1
    return render_template(
        "matrix.html",
        portal_skills=PORTAL_SKILLS,
        stage_counts=stage_counts,
        active_page="matrix",
    )

# ── API ───────────────────────────────────────────────────────────────────────

@app.post("/api/completion")
def api_completion():
    body       = request.get_json(force=True)
    client     = body.get("client_name", "")
    stage_name = body.get("stage", "")
    task_num   = str(body.get("task_num", ""))
    checked    = bool(body.get("checked", False))
    if not client or not stage_name or not task_num:
        return jsonify({"ok": False, "error": "missing fields"}), 400
    toggle_completion(client, stage_name, task_num, checked)
    return jsonify({"ok": True})


@app.post("/api/stage-response")
def api_stage_response():
    body     = request.get_json(force=True)
    client   = str(body.get("client_name", "")).strip()
    selected = str(body.get("selected_stage", "")).strip()
    if not client or selected not in STAGES:
        return jsonify({"ok": False, "error": "invalid fields"}), 400
    record_stage_response(client, selected)
    return jsonify({"ok": True, "selected_stage": selected})


# ── Ask bar ───────────────────────────────────────────────────────────────────

@app.route("/api/ask", methods=["POST"])
def api_ask():
    """Stream an answer about the current book back to the overview page.

    Emits text/event-stream. Every failure path yields an `error` event rather
    than a non-200, so the bar always has something to render — a clone with no
    API credential configured gets a setup hint, not a stack trace.
    """
    import assistant

    question = (request.get_json(silent=True) or {}).get("question", "").strip()
    if not question:
        return jsonify({"ok": False, "error": "Ask a question first."}), 400
    if len(question) > 2000:
        return jsonify({"ok": False, "error": "Question is too long."}), 400

    def sse(event: str, data: str) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def generate():
        if not assistant.is_configured():
            yield sse("error",
                      "No Anthropic credential found. Set ANTHROPIC_API_KEY in your "
                      "environment (see .env.example), then restart the dashboard.")
            yield sse("done", "")
            return
        try:
            for chunk in assistant.stream_answer(question):
                yield sse("delta", chunk)
        except Exception as exc:                      # noqa: BLE001
            # Surface the failure type but never the message — an SDK error can
            # echo request details, and this response goes to the browser.
            yield sse("error", f"The assistant failed ({type(exc).__name__}).")
        yield sse("done", "")

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.errorhandler(413)
def too_large(_):
    if request.path.startswith("/skills/"):
        return jsonify({"ok": False, "error": "Upload too large. Try fewer files."}), 413
    return "Request entity too large", 413


# ── Launch ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:8000")).start()
    app.run(host="127.0.0.1", port=8000, debug=True, use_reloader=False)
