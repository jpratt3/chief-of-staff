"""Four mock carrier portals, each with a different real-world obstacle.

  Northbridge  8201  username/password + TOTP 2FA, async report generation
  Harborstone  8202  serves TWO documents; the agent must take both
  Bluehaven    8203  asks a security question the vault cannot answer
  Redstone     8204  rejects policy numbers containing separators

Server-rendered HTML only -- no JS required, so Playwright drives them the same
way a person would.
"""
from __future__ import annotations

import secrets
import sys
import time
from datetime import date

import pyotp
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import config
import seed

SESSIONS: dict[str, dict] = {}

CSS = """
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#eef1f4;margin:0;color:#1c2430}
 .bar{background:#1f3350;color:#fff;padding:12px 24px;font-weight:600;letter-spacing:.3px}
 .wrap{max-width:760px;margin:32px auto;background:#fff;border:1px solid #d3dae2;padding:28px 32px}
 h2{margin:0 0 4px;font-size:19px}
 .sub{color:#5b6675;font-size:13px;margin-bottom:20px}
 label{display:block;font-size:12px;text-transform:uppercase;color:#5b6675;margin:14px 0 4px;letter-spacing:.4px}
 input,select{padding:8px 10px;border:1px solid #b9c3cf;width:280px;font-size:14px;background:#fff}
 button{margin-top:20px;background:#1f3350;color:#fff;border:0;padding:10px 20px;font-size:14px;cursor:pointer}
 .err{background:#fdecea;border:1px solid #f5c2bd;color:#8a231a;padding:12px 14px;margin:16px 0;font-size:14px}
 .ok{background:#eaf6ec;border:1px solid #bfe0c6;color:#1f5b2c;padding:12px 14px;margin:16px 0;font-size:14px}
 table{border-collapse:collapse;width:100%;margin-top:18px;font-size:14px}
 th,td{border-bottom:1px solid #e2e7ec;padding:9px 8px;text-align:left}
 th{font-size:11px;text-transform:uppercase;color:#5b6675}
 a{color:#1a5fb4}
 .doc{padding:10px 0}
</style>"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><title>{title}</title>{CSS}</head>"
                        f"<body><div class='bar'>{title}</div><div class='wrap'>{body}</div>"
                        f"</body></html>")


def _sid(request: Request) -> str | None:
    sid = request.cookies.get("sid")
    return sid if sid in SESSIONS else None


def _vault(carrier: str) -> dict:
    import json
    data = json.loads(config.VAULT_PATH.read_text())
    return data.get(carrier, {})


def make_app(carrier: str) -> FastAPI:
    meta = config.CARRIERS[carrier]
    quirk = meta["quirk"]
    app = FastAPI(title=f"{carrier} Portal")
    host = meta["host"]

    def login_page(error: str = "") -> HTMLResponse:
        err = f"<div class='err'>{error}</div>" if error else ""
        return page(f"{host}", f"""
          <h2>{carrier} Broker Portal</h2>
          <div class='sub'>Authorized agents only</div>{err}
          <form method='post' action='/login'>
            <label for='username'>Username</label>
            <input id='username' name='username' autocomplete='off'>
            <label for='password'>Password</label>
            <input id='password' name='password' type='password'>
            <br><button type='submit'>Sign In</button>
          </form>""")

    @app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        if _sid(request):
            return RedirectResponse(_landing(carrier), status_code=303)
        return login_page()

    @app.post("/login")
    def login(username: str = Form(""), password: str = Form("")):
        v = _vault(carrier)
        if username != v.get("username") or password != v.get("password"):
            return login_page("Invalid username or password.")
        sid = secrets.token_hex(8)
        SESSIONS[sid] = {"carrier": carrier, "authed": quirk != "totp", "step": "login"}
        dest = "/totp" if quirk == "totp" else (
            "/security-question" if quirk == "security_question" else _landing(carrier))
        r = RedirectResponse(dest, status_code=303)
        r.set_cookie("sid", sid)
        return r

    # ---- Northbridge: TOTP -------------------------------------------------
    @app.get("/totp", response_class=HTMLResponse)
    def totp_form(request: Request, error: str = ""):
        if not _sid(request):
            return RedirectResponse("/", status_code=303)
        err = f"<div class='err'>{error}</div>" if error else ""
        return page(host, f"""
          <h2>Two-Factor Authentication</h2>
          <div class='sub'>Enter the 6-digit code from your authenticator app.</div>{err}
          <form method='post' action='/totp'>
            <label for='code'>Authentication Code</label>
            <input id='code' name='code' autocomplete='off' maxlength='6'>
            <br><button type='submit'>Verify</button>
          </form>""")

    @app.post("/totp")
    def totp_check(request: Request, code: str = Form("")):
        sid = _sid(request)
        if not sid:
            return RedirectResponse("/", status_code=303)
        secret = _vault(carrier).get("totp_secret", "")
        if not secret or not pyotp.TOTP(secret).verify(code.strip(), valid_window=2):
            return totp_form(request, error="That code is not valid. Try again.")
        SESSIONS[sid]["authed"] = True
        return RedirectResponse("/search", status_code=303)

    # ---- Bluehaven: unanswerable security question -------------------------
    @app.get("/security-question", response_class=HTMLResponse)
    def security_question(request: Request, error: str = ""):
        if not _sid(request):
            return RedirectResponse("/", status_code=303)
        err = f"<div class='err'>{error}</div>" if error else ""
        return page(host, f"""
          <h2>Additional Verification Required</h2>
          <div class='sub'>For your security, answer the question on file.</div>{err}
          <p><strong>What was the name of your first pet?</strong></p>
          <form method='post' action='/security-question'>
            <label for='answer'>Answer</label>
            <input id='answer' name='answer' autocomplete='off'>
            <br><button type='submit'>Continue</button>
          </form>""")

    @app.post("/security-question")
    def security_answer(request: Request, answer: str = Form("")):
        # There is no correct answer available to the agent -- by design.
        return security_question(request, error="That answer does not match our records.")

    # ---- search / report ---------------------------------------------------
    @app.get("/search", response_class=HTMLResponse)
    def search_form(request: Request, error: str = "", value: str = ""):
        sid = _sid(request)
        if not sid or not SESSIONS[sid]["authed"]:
            return RedirectResponse("/", status_code=303)
        err = f"<div class='err'>{error}</div>" if error else ""
        return page(host, f"""
          <h2>Policy Search</h2>
          <div class='sub'>Search for a policy to request loss experience.</div>{err}
          <form method='post' action='/search'>
            <label for='policy'>Policy Number</label>
            <input id='policy' name='policy' autocomplete='off' value='{value}'>
            <br><button type='submit'>Search</button>
          </form>""")

    @app.post("/search")
    def search(request: Request, policy: str = Form("")):
        sid = _sid(request)
        if not sid or not SESSIONS[sid]["authed"]:
            return RedirectResponse("/", status_code=303)
        q = policy.strip()

        if quirk == "dash_rejection" and any(ch in q for ch in "- ./"):
            return search_form(
                request, value=q,
                error=("Invalid policy number format. "
                       "Enter as RS0000000, no separators."))

        known = {}
        for p in seed.MERIDIAN_POLICIES:
            if p.carrier != carrier:
                continue
            known[(p.portal_search_value or p.policy_number).upper()] = p
        pol = known.get(q.upper())
        if not pol:
            return search_form(request, value=q,
                               error="No matching policy found for that number.")
        return RedirectResponse(f"/report/{pol.policy_number}", status_code=303)

    @app.get("/report/{policy_number}", response_class=HTMLResponse)
    def report_form(request: Request, policy_number: str):
        sid = _sid(request)
        if not sid or not SESSIONS[sid]["authed"]:
            return RedirectResponse("/", status_code=303)
        return page(host, f"""
          <h2>Loss Run Report</h2>
          <div class='sub'>POLICY {policy_number}</div>
          <form method='post' action='/report/{policy_number}'>
            <label for='from_date'>From</label>
            <input id='from_date' name='from_date' placeholder='MM/DD/YYYY'>
            <label for='to_date'>To</label>
            <input id='to_date' name='to_date' placeholder='MM/DD/YYYY'>
            <label for='format'>Format</label>
            <select id='format' name='format'>
              <option value='summary'>PDF &middot; Summary</option>
              <option value='detail'>PDF &middot; Detail</option>
            </select>
            <br><button type='submit'>Generate Report</button>
          </form>""")

    @app.post("/report/{policy_number}")
    def generate(request: Request, policy_number: str,
                 from_date: str = Form(""), to_date: str = Form(""),
                 format: str = Form("summary")):
        sid = _sid(request)
        if not sid or not SESSIONS[sid]["authed"]:
            return RedirectResponse("/", status_code=303)
        if not from_date.strip() or not to_date.strip():
            return page(host, """<h2>Loss Run Report</h2>
              <div class='err'>Both From and To dates are required.</div>
              <p><a href='javascript:history.back()'>Back</a></p>""")
        time.sleep(0.6)  # "generating..."
        SESSIONS[sid]["last_report"] = {
            "policy": policy_number, "from": from_date, "to": to_date, "format": format,
        }
        return RedirectResponse(f"/ready/{policy_number}", status_code=303)

    @app.get("/ready/{policy_number}", response_class=HTMLResponse)
    def ready(request: Request, policy_number: str):
        sid = _sid(request)
        if not sid or not SESSIONS[sid]["authed"]:
            return RedirectResponse("/", status_code=303)
        fn = seed.artifact_name(carrier)
        p = config.SEED_PDF_DIR / fn
        kb = p.stat().st_size // 1024 if p.exists() else 0
        return page(host, f"""
          <h2>Report Ready</h2>
          <div class='ok'>Report generated in 41s</div>
          <div class='sub'>POLICY {policy_number} &middot; valued {seed.VALUATION_CURRENT:%m/%d/%Y}</div>
          <table><tr><th>Document</th><th>Size</th><th></th></tr>
          <tr><td>{fn}</td><td>{kb} KB</td>
          <td><a id='download-0' href='/download/{fn}'>Download</a></td></tr></table>""")

    # ---- Harborstone: two documents ---------------------------------------
    @app.get("/claims-history", response_class=HTMLResponse)
    def claims_history(request: Request):
        sid = _sid(request)
        if not sid or not SESSIONS[sid]["authed"]:
            return RedirectResponse("/", status_code=303)
        names = [seed.artifact_name(carrier),
                 seed.artifact_name(carrier, "large_loss_detail")]
        rows = ""
        for i, fn in enumerate(names):
            p = config.SEED_PDF_DIR / fn
            kb = p.stat().st_size // 1024 if p.exists() else 0
            kind = "Currently valued loss run" if i == 0 else "Large loss detail report"
            rows += (f"<tr><td>{fn}<br><span class='sub'>{kind}</span></td><td>{kb} KB</td>"
                     f"<td><a id='download-{i}' href='/download/{fn}'>Download</a></td></tr>")
        return page(host, f"""
          <h2>Claims History</h2>
          <div class='sub'>Meridian Logistics &middot; all documents on file</div>
          <table><tr><th>Document</th><th>Size</th><th></th></tr>{rows}</table>""")

    @app.get("/download/{filename}")
    def download(request: Request, filename: str):
        sid = _sid(request)
        if not sid or not SESSIONS[sid]["authed"]:
            return RedirectResponse("/", status_code=303)
        p = config.SEED_PDF_DIR / filename
        if not p.exists():
            return page(host, "<div class='err'>File not found.</div>")
        return FileResponse(p, media_type="application/pdf", filename=filename)

    return app


def _landing(carrier: str) -> str:
    return "/claims-history" if config.CARRIERS[carrier]["quirk"] == "two_documents" else "/search"


if __name__ == "__main__":
    import uvicorn
    name = sys.argv[1]
    uvicorn.run(make_app(name), host="127.0.0.1",
                port=config.CARRIERS[name]["port"], log_level="warning")
