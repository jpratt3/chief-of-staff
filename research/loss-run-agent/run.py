"""CLI entry point.

  python run.py world     start the mock world (Epic + 4 carrier portals)
  python run.py demo      run the whole book end to end and print the board
  python run.py approve   approve every open discrepancy and deliver
  python run.py web       serve the review UI on :8000
  python run.py reset     wipe runs, mail, sharepoint, state
  python run.py test      run the test suite
"""
from __future__ import annotations

import asyncio
import json
import sys

import config
import orchestrator as orch
import services

BOLD, DIM, AMBER, GREEN, RESET = "\033[1m", "\033[2m", "\033[33m", "\033[32m", "\033[0m"


def _board(jobs) -> None:
    print()
    print(f"{BOLD}APPLIED EPIC · EXPIRATION REPORT{RESET}   Renewals in 90 days   "
          f"{DIM}(today {orch.TODAY:%m/%d/%Y}){RESET}")
    print("-" * 78)
    print(f"{'ACCOUNT':<26}{'EXPIRES':<14}{'LOSS RUNS'}")
    for j in jobs:
        if j.state == orch.QUEUED and j.activates:
            label = f"Queued for {j.activates[5:7]}/{j.activates[8:10]}"
        elif j.state == orch.DELIVERED:
            label = j.deliverable if j.deliverable.startswith(("Summary", "(")) else "Summary filed"
        elif j.state == orch.NEEDS_REVIEW:
            n = sum(1 for d in j.discrepancies if not d["resolved"])
            label = f"{AMBER}Needs review · {n} discrepanc{'y' if n == 1 else 'ies'}{RESET}"
        elif j.carriers:
            label = f"Gathering · {j.progress}"
        else:
            label = j.deliverable or j.state
        exp = j.expires
        print(f"{j.account:<26}{exp[5:7]}/{exp[8:10]}/{exp[:4]:<9}{label}")
    print()


def _carrier_table(job) -> None:
    print(f"{BOLD}Gathering loss runs{RESET}   {job.account} · renewal "
          f"{job.expires[5:7]}/{job.expires[8:10]}   [{job.progress}]")
    print("-" * 78)
    for c in job.carriers:
        mark = AMBER if c.status == "input_required" else (
            GREEN if c.status in ("retrieved", "received") else "")
        chan = c.channel.title()
        print(f"  {c.carrier:<14}{c.policy_number:<16}{chan:<8}"
              f"{mark}{c.label}{RESET if mark else ''}")
    print()


def _review(job) -> None:
    if not job.discrepancies:
        return
    n = len(job.discrepancies)
    for i, d in enumerate(job.discrepancies, 1):
        state = f"{GREEN}approved{RESET}" if d["resolved"] else f"{AMBER}open{RESET}"
        print(f"{AMBER}!{RESET} {BOLD}{d['headline']}{RESET}"
              f"{DIM}   {i} of {n}   [{state}]{RESET}")
        dol = d.get("date_of_loss") or ""
        print(f"  Claim {d['claim_number']}  {DIM}{d['carrier']} · {dol}{RESET}")
        print(f"    {d['pick_label']:<22}{d['pick_value']:<14}"
              f"{DIM}{d['pick_ref']['artifact']} · p.{d['pick_ref']['page']}{RESET}"
              f"   {GREEN}[Agent's pick]{RESET}")
        print(f"    {d['other_label']:<22}{d['other_value']:<14}"
              f"{DIM}{d['other_ref']['artifact']} · p.{d['other_ref']['page']}{RESET}"
              f"   {DIM}[Superseded]{RESET}")
        print(f"    {DIM}{d['rationale']}{RESET}")
        print()
    if job.development:
        print(f"{DIM}Not flagged (normal development):{RESET}")
        for n_ in job.development:
            print(f"  {DIM}· {n_}{RESET}")
        print()


def cmd_demo() -> None:
    services.start()
    orch.reset_all()
    print(f"{DIM}running the book...{RESET}")
    jobs = asyncio.run(orch.run_book())
    orch.save_state({"jobs": {j.account_id: orch.job_to_dict(j) for j in jobs}})
    _board(jobs)
    m = next((j for j in jobs if j.account_id == "meridian"), None)
    if m:
        _carrier_table(m)
        if m.totals:
            t = m.totals
            print(f"{BOLD}Loss Run Summary{RESET}  {t['claims']} claims · "
                  f"paid ${t['paid']:,.0f} · incurred ${t['incurred']:,.0f} "
                  f"({t['open']} open)")
            print()
        _review(m)
        if m.state == orch.NEEDS_REVIEW:
            print(f"{DIM}Run `python run.py approve` to approve and deliver.{RESET}")
    print(f"{DIM}Review UI: python run.py web   ->  http://127.0.0.1:{config.WEB_PORT}{RESET}")


def cmd_approve() -> None:
    state = orch.load_state()
    jd = state["jobs"].get("meridian")
    if not jd:
        print("no run found -- `python run.py demo` first")
        return
    job = _rehydrate(jd)
    if not orch.get_cached("meridian"):
        print("reconciliation cache is empty (fresh process) -- re-running the book")
        services.start()
        jobs = asyncio.run(orch.run_book())
        job = next(j for j in jobs if j.account_id == "meridian")
    for d in list(job.discrepancies):
        orch.approve_discrepancy(job, d["id"])
    if orch.all_resolved(job):
        path = orch.deliver(job)
        print(f"{GREEN}All {len(job.discrepancies)} discrepancies approved.{RESET}")
        print(f"Delivered: {path}")
        print(f"Attached to Applied Epic account 'meridian' as "
              f"{path.rsplit('/', 1)[-1]} (Added by LossRunAgent)")
    orch.save_state({"jobs": {**state["jobs"], "meridian": orch.job_to_dict(job)}})


def _rehydrate(d: dict) -> orch.Job:
    j = orch.Job(account_id=d["account_id"], account=d["account"], expires=d["expires"],
                 state=d["state"], activates=d.get("activates", ""))
    j.carriers = [orch.CarrierStatus(**c) for c in d.get("carriers", [])]
    j.discrepancies = d.get("discrepancies", [])
    j.development = d.get("development", [])
    j.totals = d.get("totals", {})
    j.deliverable = d.get("deliverable", "")
    j.chase_log = d.get("chase_log", [])
    return j


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "world":
        for n, d in services.start().items():
            print(f"  {n:<14}:{d['port']}  {'reused' if d['reused'] else 'started'}")
    elif cmd == "demo":
        cmd_demo()
    elif cmd == "approve":
        cmd_approve()
    elif cmd == "reset":
        orch.reset_all()
        print("reset")
    elif cmd == "stop":
        services.stop()
        print("stopped")
    elif cmd == "web":
        import uvicorn
        services.start()
        uvicorn.run("web:app", host="127.0.0.1", port=config.WEB_PORT, log_level="warning")
    elif cmd == "test":
        import subprocess
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "tests"],
                                 cwd=config.ROOT))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
