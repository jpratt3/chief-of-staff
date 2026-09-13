"""Review UI: renewal board, carrier tracker, run trace, review queue, AOP viewer.

Visual target: quiet, typographic, near-monochrome,
with a single amber accent reserved for "needs you".
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import config
import orchestrator as orch

app = FastAPI(title="Loss Run Agent")
JOBS: dict[str, orch.Job] = {}
RUNNING = {"busy": False}

CSS = """
<style>
:root{--bg:#f2f2ee;--card:#fff;--ink:#14181d;--dim:#6b7280;--line:#e3e3dd;
      --amber:#b45309;--amber-bg:#fdf6ec;--amber-line:#eedcc2;--green:#3f6b4a;--pick:#eef3ee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:inherit}
.top{border-bottom:1px solid var(--line);background:#fff}
.top .in{max-width:1080px;margin:0 auto;padding:14px 24px;display:flex;gap:26px;align-items:center}
.brand{font-family:Georgia,serif;font-size:20px;letter-spacing:.06em}
.top nav a{color:var(--dim);text-decoration:none;font-size:13px;letter-spacing:.04em;
           text-transform:uppercase}
.top nav a:hover,.top nav a.on{color:var(--ink)}
.wrap{max-width:1080px;margin:0 auto;padding:34px 24px 70px}
h1{font-family:Georgia,serif;font-weight:400;font-size:31px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13.5px;margin-bottom:26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:3px;margin-bottom:20px}
.card .hd{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
          justify-content:space-between;align-items:center}
.card .hd h2{font-size:15px;margin:0;font-weight:600}
table{width:100%;border-collapse:collapse}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);
   text-align:left;padding:11px 20px;font-weight:500;border-bottom:1px solid var(--line)}
td{padding:12px 20px;border-bottom:1px solid #f0f0ea;font-size:14px}
tr:last-child td{border-bottom:0}
.chip{display:inline-block;padding:2px 9px;border-radius:11px;font-size:11.5px;
      background:#f0f0ea;color:#4b5563}
.chip.amber{background:var(--amber-bg);color:var(--amber);border:1px solid var(--amber-line)}
.chip.green{background:var(--pick);color:var(--green)}
.mono{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:12.5px}
.dim{color:var(--dim)}
.btn{display:inline-block;padding:9px 17px;background:#31432f;color:#fff;border:0;
     border-radius:3px;font-size:13.5px;cursor:pointer;text-decoration:none}
.btn.ghost{background:#fff;color:var(--ink);border:1px solid #cfcfc6}
.btn:disabled{opacity:.45;cursor:default}
.disc{border:1px solid var(--amber-line);border-radius:3px;margin:18px 20px;overflow:hidden}
.disc .dh{background:var(--amber-bg);padding:11px 16px;display:flex;justify-content:space-between;
          color:var(--amber);font-weight:600;font-size:14px}
.src{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;
     margin:11px 16px;border:1px solid var(--line);border-radius:3px;background:#fbfbf8}
.src.pick{background:var(--pick);border-color:#d4e0d4}
.src .v{font-size:16px;font-weight:600}
.acts{padding:4px 16px 15px;display:flex;gap:10px;align-items:center}
.trace{display:grid;grid-template-columns:330px 1fr;gap:0}
.steps{border-right:1px solid var(--line);max-height:640px;overflow:auto}
.step{padding:10px 18px;border-bottom:1px solid #f0f0ea;font-size:13px;cursor:pointer;
      display:block;text-decoration:none;color:inherit}
.step:hover{background:#faf9f5}
.step.on{background:#f3f5f1;border-left:3px solid #31432f;padding-left:15px}
.shot{padding:18px;text-align:center}
.shot img{max-width:100%;border:1px solid var(--line);border-radius:2px}
.aop{padding:26px 30px;font-size:14.5px;line-height:1.72;max-width:800px}
.aop h1,.aop h2{font-family:Georgia,serif;font-weight:400}
.aop h2{font-size:19px;margin:26px 0 8px}
.aop code.d{background:var(--pick);color:var(--green);padding:1px 6px;border-radius:3px;
            font-size:12.5px;font-family:ui-monospace,monospace}
.aop code.a{background:#eef1f6;color:#33507a;padding:1px 6px;border-radius:3px;
            font-size:12.5px;font-family:ui-monospace,monospace}
.aop .crit{font-weight:700}
.aop pre{background:#fbfbf8;border:1px solid var(--line);padding:14px;overflow:auto;font-size:12.5px}
.empty{padding:40px 20px;text-align:center;color:var(--dim)}
</style>"""


def layout(title: str, body: str, active: str = "") -> HTMLResponse:
    def a(href, label, key):
        return f"<a href='{href}' class='{'on' if key == active else ''}'>{label}</a>"
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{title}</title>{CSS}</head><body>
<div class='top'><div class='in'><span class='brand'>LOSSRUNAGENT</span><nav>
{a('/', 'Renewals', 'board')} &nbsp; {a('/review/meridian', 'Review', 'review')}
&nbsp; {a('/aop', 'Procedure', 'aop')}</nav></div></div>
<div class='wrap'>{body}</div></body></html>""")


def _label(j: orch.Job) -> str:
    if j.state == orch.QUEUED and j.activates:
        return f"<span class='chip'>Queued for {j.activates[5:7]}/{j.activates[8:10]}</span>"
    if j.state == orch.NEEDS_REVIEW:
        n = sum(1 for d in j.discrepancies if not d["resolved"])
        return (f"<span class='chip amber'>Needs review · {n} "
                f"discrepanc{'y' if n == 1 else 'ies'}</span>")
    if j.state == orch.DELIVERED:
        return "<span class='chip green'>Summary filed</span>"
    if j.state == orch.RETRIEVING and not j.carriers:
        return f"<span class='chip'>{j.deliverable}</span>"
    if j.carriers:
        return f"<span class='chip'>Gathering · {j.progress}</span>"
    return f"<span class='chip'>{j.state}</span>"


@app.get("/", response_class=HTMLResponse)
def board():
    if not JOBS:
        return layout("Renewals", f"""
          <h1>Renewals in 90 days</h1>
          <div class='sub'>Applied Epic · expiration report · today {orch.TODAY:%m/%d/%Y}</div>
          <div class='card'><div class='empty'>
            <p>No run yet.</p>
            <form method='post' action='/run'><button class='btn'>Run the book</button></form>
          </div></div>""", "board")

    rows = ""
    for j in JOBS.values():
        exp = j.expires
        link = (f"<a href='/account/{j.account_id}' style='text-decoration:none'>{j.account}</a>"
                if j.carriers else j.account)
        rows += (f"<tr><td>{link}</td><td class='dim'>{exp[5:7]}/{exp[8:10]}/{exp[:4]}</td>"
                 f"<td>{_label(j)}</td></tr>")
    return layout("Renewals", f"""
      <h1>Renewals in 90 days</h1>
      <div class='sub'>Applied Epic · expiration report · today {orch.TODAY:%m/%d/%Y}
        &nbsp;·&nbsp; <span class='chip green'>Watching</span></div>
      <div class='card'>
        <div class='hd'><h2>Expiration report</h2>
          <form method='post' action='/run'>
            <button class='btn ghost'>Re-run the book</button></form></div>
        <table><tr><th>Account</th><th>Expires</th><th>Loss runs</th></tr>{rows}</table>
      </div>""", "board")


@app.post("/run")
def run_book():
    if RUNNING["busy"]:
        return RedirectResponse("/", status_code=303)
    RUNNING["busy"] = True
    try:
        orch.reset_all()
        jobs = asyncio.run(orch.run_book())
        JOBS.clear()
        for j in jobs:
            JOBS[j.account_id] = j
    finally:
        RUNNING["busy"] = False
    return RedirectResponse("/", status_code=303)


@app.get("/account/{account_id}", response_class=HTMLResponse)
def account(account_id: str):
    j = JOBS.get(account_id)
    if not j:
        return RedirectResponse("/", status_code=303)
    rows = ""
    for c in j.carriers:
        cls = ("amber" if c.status == "input_required"
               else "green" if c.status in ("retrieved", "received") else "")
        trace = (f"<a class='dim' href='/run/{c.run_id}'>trace</a>" if c.run_id else "")
        arts = "<br>".join(f"<span class='mono dim'>{a}</span>" for a in c.artifacts) or "—"
        rows += (f"<tr><td>{c.carrier}</td><td class='mono'>{c.policy_number}</td>"
                 f"<td><span class='chip'>{c.channel.title()}</span></td>"
                 f"<td><span class='chip {cls}'>{c.label}</span></td>"
                 f"<td>{arts}</td><td>{trace}</td></tr>")
    t = j.totals
    totals = ""
    if t:
        badge = ("Renewal ready" if j.state == orch.DELIVERED
                 else j.state.replace("_", " ").capitalize())
        tone = "green" if j.state == orch.DELIVERED else "amber"
        totals = (f"<div class='card'><div class='hd'><h2>Loss Run Summary</h2>"
                  f"<span class='chip {tone}'>{badge}</span></div>"
                  f"<table><tr><th>Claims</th><th>Open</th><th>Paid</th><th>Incurred</th></tr>"
                  f"<tr><td>{t['claims']}</td><td>{t['open']}</td>"
                  f"<td>${t['paid']:,.0f}</td><td>${t['incurred']:,.0f}</td></tr></table></div>")
    deliver = ""
    if j.deliverable and j.deliverable.endswith(".xlsx"):
        deliver = (f"<div class='card'><div class='hd'><h2>Delivered</h2></div>"
                   f"<table><tr><td class='mono'>{j.deliverable}</td>"
                   f"<td class='dim'>SharePoint</td></tr>"
                   f"<tr><td class='mono'>{j.deliverable.rsplit('/', 1)[-1]}</td>"
                   f"<td class='dim'>Applied Epic · added by LossRunAgent</td></tr></table></div>")
    chase = ""
    if j.chase_log:
        items = "".join(f"<tr><td class='dim'>{l}</td></tr>" for l in j.chase_log)
        chase = (f"<div class='card'><div class='hd'><h2>Email chase</h2></div>"
                 f"<table>{items}</table></div>")
    review = ""
    if j.discrepancies:
        n = sum(1 for d in j.discrepancies if not d["resolved"])
        review = (f"<p><a class='btn' href='/review/{account_id}'>Review "
                  f"{n} open discrepanc{'y' if n == 1 else 'ies'}</a></p>")
    return layout(j.account, f"""
      <h1>{j.account}</h1>
      <div class='sub'>renewal {j.expires[5:7]}/{j.expires[8:10]} · {j.progress} retrieved</div>
      <div class='card'><div class='hd'><h2>Gathering loss runs</h2>
        <span class='chip'>{j.progress}</span></div>
        <table><tr><th>Carrier</th><th>Policy</th><th>Channel</th><th>Status</th>
        <th>Documents</th><th></th></tr>{rows}</table></div>
      {totals}{review}{deliver}{chase}""", "board")


@app.get("/run/{run_id}", response_class=HTMLResponse)
def run_trace(run_id: str, seq: int = 0):
    d = config.RUNS_DIR / run_id
    tp = d / "trace.jsonl"
    if not tp.exists():
        return layout("Trace", "<div class='card'><div class='empty'>No trace.</div></div>")
    events = [json.loads(l) for l in tp.read_text(encoding="utf-8").splitlines() if l.strip()]
    result = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else {}

    shots = [e for e in events if e.get("screenshot")]
    sel = next((e for e in events if e["seq"] == seq), (shots[-1] if shots else events[-1]))

    steps = ""
    for e in events:
        note = e["result"].split("\n")[0][:64]
        cls = "on" if e["seq"] == sel["seq"] else ""
        steps += (f"<a class='step {cls}' href='/run/{run_id}?seq={e['seq']}'>"
                  f"<div><strong>{e['tool']}</strong> "
                  f"<span class='dim'>· {e['ts'][11:]}</span></div>"
                  f"<div class='dim'>{note}</div></a>")

    # Steps without a screenshot (fills, reads) show the last frame captured
    # before them, so the pane always shows what the agent was looking at.
    frame, carried = sel.get("screenshot"), False
    if not frame:
        prior = [e for e in events if e["seq"] <= sel["seq"] and e.get("screenshot")]
        if prior:
            frame, carried = prior[-1]["screenshot"], True
    img = (f"<img src='/shot/{run_id}/{frame.split('/')[-1]}'>"
           + (f"<div class='dim' style='margin-top:8px;font-size:12.5px'>"
              f"last captured frame</div>" if carried else "")
           if frame else "<p class='dim'>No screenshot for this step.</p>")
    detail = (f"<div style='padding:0 18px 18px' class='mono dim'>"
              f"{json.dumps(sel['args'])}<br><br>{sel['result'][:700]}</div>")
    return layout(f"Trace {run_id}", f"""
      <h1>Run trace</h1>
      <div class='sub'>{result.get('carrier','')} · {result.get('policy_number','')} ·
        planner <span class='chip'>{result.get('planner','')}</span> ·
        <span class='chip {"green" if result.get("status")=="retrieved" else "amber"}'>
        {result.get('status','')}</span></div>
      <div class='card'><div class='trace'>
        <div class='steps'>{steps}</div>
        <div><div class='shot'>{img}</div>{detail}</div>
      </div></div>
      <p><a class='btn ghost' href='/account/{result.get("account_id","meridian")}'>Back</a></p>""",
                  "board")


@app.get("/shot/{run_id}/{name}")
def shot(run_id: str, name: str):
    p = config.RUNS_DIR / run_id / "artifacts" / "screens" / name
    if not p.exists():
        return HTMLResponse("not found", status_code=404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/review/{account_id}", response_class=HTMLResponse)
def review(account_id: str):
    j = JOBS.get(account_id)
    if not j:
        return RedirectResponse("/", status_code=303)
    if not j.discrepancies:
        return layout("Review", "<h1>Review</h1><div class='card'>"
                                "<div class='empty'>Nothing to review.</div></div>", "review")
    n = len(j.discrepancies)
    cards = ""
    for i, d in enumerate(j.discrepancies, 1):
        done = d["resolved"]
        acts = (f"<span class='chip green'>Approved {d['approved_value']}</span>"
                if done else
                f"<form method='post' action='/approve/{account_id}/{d['id']}' "
                f"style='display:flex;gap:10px'>"
                f"<button class='btn'>Approve {d['pick_value']}</button>"
                f"<a class='btn ghost' href='/source/{account_id}/{d['id']}'>Open source page</a>"
                f"</form>")
        cards += f"""
        <div class='disc'>
          <div class='dh'><span>{d['headline']}</span><span class='dim'>{i} of {n}</span></div>
          <div style='padding:12px 16px 0'><strong>Claim {d['claim_number']}</strong>
            <span class='dim'>&nbsp; {d['carrier']} · {d.get('date_of_loss') or ''}</span></div>
          <div class='src pick'><div><div>{d['pick_label']}</div>
            <div class='mono dim'>{d['pick_ref']['artifact']} · p.{d['pick_ref']['page']}</div></div>
            <div style='display:flex;gap:14px;align-items:center'>
              <span class='v'>{d['pick_value']}</span>
              <span class='chip green'>Agent's pick</span></div></div>
          <div class='src'><div><div>{d['other_label']}</div>
            <div class='mono dim'>{d['other_ref']['artifact']} · p.{d['other_ref']['page']}</div></div>
            <div style='display:flex;gap:14px;align-items:center'>
              <span class='v dim'>{d['other_value']}</span>
              <span class='chip'>Superseded</span></div></div>
          <div style='padding:2px 16px 0' class='dim' >{d['rationale']}</div>
          <div class='acts'>{acts}<span class='dim' style='margin-left:auto'>
            Logged to audit trail</span></div>
        </div>"""

    dev = ""
    if j.development:
        items = "".join(f"<tr><td class='dim'>{x}</td></tr>" for x in j.development)
        dev = (f"<div class='card'><div class='hd'><h2>Not flagged · normal development</h2>"
               f"</div><table>{items}</table></div>")

    footer = ""
    if all(d["resolved"] for d in j.discrepancies) and j.state != orch.DELIVERED:
        footer = (f"<form method='post' action='/deliver/{account_id}'>"
                  f"<button class='btn'>Deliver summary</button></form>")
    elif j.state == orch.DELIVERED:
        footer = (f"<p class='dim'>Delivered to <span class='mono'>{j.deliverable}</span> "
                  f"and attached to the Applied Epic account.</p>")

    return layout("Review", f"""
      <h1>Review queue</h1>
      <div class='sub'>{j.account} · carrier documents disagree · every value cites its source page</div>
      <div class='card'>{cards}</div>{dev}{footer}""", "review")


@app.post("/approve/{account_id}/{disc_id}")
def approve(account_id: str, disc_id: str):
    j = JOBS.get(account_id)
    if j:
        orch.approve_discrepancy(j, disc_id)
    return RedirectResponse(f"/review/{account_id}", status_code=303)


@app.post("/deliver/{account_id}")
def deliver(account_id: str):
    j = JOBS.get(account_id)
    if j and orch.all_resolved(j):
        orch.deliver(j)
    return RedirectResponse(f"/account/{account_id}", status_code=303)


@app.get("/source/{account_id}/{disc_id}")
def source(account_id: str, disc_id: str):
    """Open the cited artifact so the reviewer can check the page."""
    j = JOBS.get(account_id)
    if not j:
        return RedirectResponse("/", status_code=303)
    d = next((x for x in j.discrepancies if x["id"] == disc_id), None)
    if not d:
        return RedirectResponse(f"/review/{account_id}", status_code=303)
    name = d["other_ref"]["artifact"]
    for run in config.RUNS_DIR.glob("*/artifacts/" + name):
        return FileResponse(run, media_type="application/pdf")
    p = config.SEED_PDF_DIR / name
    if p.exists():
        return FileResponse(p, media_type="application/pdf")
    return RedirectResponse(f"/review/{account_id}", status_code=303)


@app.get("/aop", response_class=HTMLResponse)
def aop_view():
    import re
    text = (config.ROOT / "aops" / "loss_run_intake.md").read_text(encoding="utf-8")
    # <Carrier> is a live placeholder the executor substitutes per run; escape it
    # so the browser does not eat it as a tag.
    text = text.replace("<Carrier>", "&lt;Carrier&gt;")

    def inline(s: str) -> str:
        s = re.sub(r"`\{?(data\.[^`}]+)\}?`", r"<code class='d'>\1</code>", s)
        s = re.sub(r"`\{?(auth\.[^`}]+)\}?`", r"<code class='a'>\1</code>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*(CRITICAL.+?)\*\*", r"<span class='crit'>\1</span>", s, flags=re.S)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s, flags=re.S)
        return s

    html, in_pre, para, pre = [], False, [], []

    def flush():
        if para:
            html.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    for line in text.split("\n"):
        if line.startswith("```"):
            if in_pre:
                html.append("<pre>" + "\n".join(pre) + "</pre>")
                pre.clear()
            else:
                flush()
            in_pre = not in_pre
            continue
        if in_pre:
            pre.append(line)
            continue
        if not line.strip() or line.startswith(("#", "- ")) or line.strip() == "---":
            flush()
        l = line
        if l.startswith("## "):
            html.append(f"<h2>{inline(l[3:])}</h2>")
        elif l.startswith("# "):
            html.append(f"<h1 style='font-size:26px'>{inline(l[2:])}</h1>")
        elif l.startswith("- "):
            html.append(f"<div style='margin-left:18px'>· {inline(l[2:])}</div>")
        elif l.strip() == "---":
            html.append("<hr style='border:0;border-top:1px solid #e3e3dd;margin:26px 0'>")
        elif l.strip():
            para.append(l.strip())
    flush()
    return layout("Procedure", f"""
      <h1>Agent Operating Procedure</h1>
      <div class='sub'>This document is the program. The executor turns it into the
        system prompt; nothing about the carriers is hardcoded.</div>
      <div class='card'><div class='aop'>{''.join(html)}</div></div>""", "aop")
