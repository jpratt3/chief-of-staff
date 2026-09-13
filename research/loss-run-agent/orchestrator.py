"""Job orchestration: expiration report -> retrieval -> extraction -> review -> delivery.

Job states:
  QUEUED -> RETRIEVING -> EXTRACTING -> RECONCILING -> NEEDS_REVIEW | READY
         -> DELIVERED | FAILED

Portal carriers run concurrently. Email carriers enter the chase machine on a
virtual clock. Accounts outside the 90-day lead window sit in QUEUED with the
date they will activate.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

import config
import extract
import reconcile as recon_mod
import seed
import summary as summary_mod
from agent import chase as chase_mod
from agent import executor, mailbox
from schema import Discrepancy

STATE_PATH = config.ROOT / "orchestrator_state.json"

QUEUED, RETRIEVING, EXTRACTING = "QUEUED", "RETRIEVING", "EXTRACTING"
RECONCILING, NEEDS_REVIEW, READY = "RECONCILING", "NEEDS_REVIEW", "READY"
DELIVERED, FAILED = "DELIVERED", "FAILED"

TODAY = date(2026, 1, 10)  # fixed "today" so the demo is reproducible

# Presentation-only states for the accounts that carry no seeded policy detail.
# Meridian is the account that actually runs end to end.
BACKDROP = {
    "kestrel": (DELIVERED, "Summary filed"),
    "aldridge": (RETRIEVING, "Requests sent · 6"),
}


@dataclass
class CarrierStatus:
    carrier: str
    policy_number: str
    channel: str
    status: str = "pending"
    label: str = "Queued"
    artifacts: list[str] = field(default_factory=list)
    note: str = ""
    run_id: str = ""


@dataclass
class Job:
    account_id: str
    account: str
    expires: str
    state: str = QUEUED
    activates: str = ""
    carriers: list[CarrierStatus] = field(default_factory=list)
    discrepancies: list[dict] = field(default_factory=list)
    development: list[str] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    deliverable: str = ""
    chase_log: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def progress(self) -> str:
        done = sum(1 for c in self.carriers if c.status in ("retrieved", "received"))
        return f"{done} of {len(self.carriers)}"


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"jobs": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def job_to_dict(j: Job) -> dict:
    d = asdict(j)
    d["progress"] = j.progress
    return d


def reset_all() -> None:
    for p in (config.RUNS_DIR, config.SHAREPOINT_DIR):
        if p.exists():
            shutil.rmtree(p)
    mailbox.reset()
    STATE_PATH.unlink(missing_ok=True)
    (config.ROOT / "epic_state.json").unlink(missing_ok=True)
    config.ensure_dirs()
    try:
        httpx.post(f"{config.epic_url()}/reset", timeout=5)
        httpx.post(f"{config.epic_url()}/accounts/meridian/attachments",
                   json={"name": "Meridian_Application_Signed.pdf", "added_by": "J. Reyes",
                         "size_kb": 412}, timeout=5)
    except Exception:
        pass


# --------------------------------------------------------------------------


async def run_account(account: dict, on_update=None) -> Job:
    """Retrieve every policy for one account, then extract, reconcile, gate."""
    job = Job(account_id=account["id"], account=account["account"],
              expires=account["expires"])

    exp = date.fromisoformat(account["expires"])
    activate_on = exp - timedelta(days=config.LEAD_DAYS)
    if activate_on > TODAY:
        job.state = QUEUED
        job.activates = activate_on.isoformat()
        return job

    policies = account.get("policies", [])
    if not policies:
        # Accounts other than Meridian carry no seeded policy detail. They exist
        # so the board reads like the reference screenshot; only Meridian runs.
        state, label = BACKDROP.get(account["id"], (DELIVERED, "Summary filed"))
        job.state = state
        job.deliverable = label
        return job

    job.state = RETRIEVING
    for p in policies:
        job.carriers.append(CarrierStatus(
            carrier=p["carrier"], policy_number=p["policy_number"],
            channel=p["channel"], status="pending", label="Queued"))
    if on_update:
        on_update(job)

    portal = [p for p in policies if p["channel"] == "portal"]
    email = [p for p in policies if p["channel"] == "email"]

    def cs(policy_number: str) -> CarrierStatus:
        return next(c for c in job.carriers if c.policy_number == policy_number)

    # ---- portal carriers, concurrently ------------------------------------
    async def do_portal(p):
        st = cs(p["policy_number"])
        st.status, st.label = "running", "Downloading"
        if on_update:
            on_update(job)
        try:
            res = await executor.run_policy(account, p)
        except Exception as e:  # a broken portal must not sink the batch
            st.status, st.label, st.note = "failed", "Failed", f"{type(e).__name__}: {e}"
            return
        st.run_id = res.run_id
        st.artifacts = res.artifacts
        st.note = res.summary
        if res.status == "retrieved":
            st.status, st.label = "retrieved", "Downloaded"
        elif res.status == "input_required":
            st.status, st.label = "input_required", "Input required"
        else:
            st.status, st.label = "failed", "Failed"
        if on_update:
            on_update(job)

    await asyncio.gather(*(do_portal(p) for p in portal))

    # ---- email carriers: send, then run the virtual clock -----------------
    chases: dict[str, chase_mod.Chase] = {}
    for p in email:
        st = cs(p["policy_number"])
        res = await executor.run_policy(account, p)
        st.run_id, st.note = res.run_id, res.summary
        st.status, st.label = "awaiting", "Request sent · 0d ago"
        chases[p["policy_number"]] = chase_mod.Chase(
            policy_number=p["policy_number"], carrier=p["carrier"],
            account=account["account"])
        if on_update:
            on_update(job)

    for day in range(1, config.ESCALATE_DAY + 1):
        for pol, ch in chases.items():
            ch.tick(day)
            st = cs(pol)
            st.label = ch.status_label
            if ch.state == chase_mod.RECEIVED:
                st.status, st.artifacts = "received", ch.attachments
            elif ch.state == chase_mod.ESCALATED:
                st.status = "escalated"
        if all(c.state in (chase_mod.RECEIVED, chase_mod.ESCALATED) for c in chases.values()):
            break
        if on_update:
            on_update(job)
    for ch in chases.values():
        job.chase_log.extend(f"{ch.carrier} {ch.policy_number}: {l}" for l in ch.log)

    # ---- extraction --------------------------------------------------------
    job.state = EXTRACTING
    if on_update:
        on_update(job)

    doc_paths: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for st in job.carriers:
        if st.status == "retrieved" and st.run_id:
            for a in st.artifacts:
                p = config.RUNS_DIR / st.run_id / "artifacts" / a
                if p.exists() and a not in seen:
                    seen.add(a)
                    doc_paths.append((p, "portal"))
        elif st.status == "received":
            for msg in mailbox.inbox_for(st.policy_number):
                if msg.get("stated_policy") != st.policy_number:
                    continue  # wrong-policy attachment already rejected
                for a in msg["attachments"]:
                    p = Path(msg["attachment_dir"]) / a
                    if p.exists() and a not in seen:
                        seen.add(a)
                        doc_paths.append((p, "email"))

    try:
        docs = extract.extract_all(doc_paths)
    except Exception as e:
        job.state, job.error = FAILED, f"extraction failed: {e}"
        return job

    # ---- reconciliation ----------------------------------------------------
    job.state = RECONCILING
    if on_update:
        on_update(job)

    rec = recon_mod.reconcile(docs)
    job.totals = rec.totals
    job.development = [n.note for n in rec.development]
    job.discrepancies = [json.loads(d.model_dump_json()) for d in rec.discrepancies]

    _cache_reconciliation(job.account_id, docs, rec)

    job.state = NEEDS_REVIEW if rec.discrepancies else READY
    if job.state == READY:
        deliver(job)
    if on_update:
        on_update(job)
    return job


_CACHE: dict[str, tuple] = {}


def _cache_reconciliation(account_id: str, docs, rec) -> None:
    _CACHE[account_id] = (docs, rec)


def get_cached(account_id: str):
    return _CACHE.get(account_id)


def deliver(job: Job) -> str:
    """Write the XLSX to SharePoint and attach it to the Applied Epic account."""
    cached = get_cached(job.account_id)
    if not cached:
        return ""
    docs, rec = cached
    year = date.fromisoformat(job.expires).year
    safe = job.account.split()[0]
    out = (config.SHAREPOINT_DIR / "Renewals" / str(year) / safe /
           f"{safe}_LossRunSummary_{year}.xlsx")
    carrier_policies = len([c for c in job.carriers])
    summary_mod.build_workbook(
        account=job.account, rec=rec, docs=docs, out_path=out,
        valuation=seed.VALUATION_CURRENT,
        period=(seed.PERIOD_START, seed.PERIOD_END),
        carrier_count=carrier_policies,
    )
    kb = max(1, out.stat().st_size // 1024)
    try:
        httpx.post(f"{config.epic_url()}/accounts/{job.account_id}/attachments",
                   json={"name": out.name, "added_by": "LossRunAgent", "size_kb": kb,
                         "note": f"{rec.totals['claims']} claims · {carrier_policies} policies"},
                   timeout=10)
        httpx.post(f"{config.epic_url()}/accounts/{job.account_id}/activities",
                   json={"text": (f"Loss run summary filed for renewal "
                                  f"{job.expires}: {rec.totals['claims']} claims, "
                                  f"incurred ${rec.totals['incurred']:,.0f}. "
                                  f"{len(job.discrepancies)} discrepancy(ies) reviewed.")},
                   timeout=10)
    except Exception:
        pass
    job.deliverable = str(out.relative_to(config.ROOT)).replace("\\", "/")
    job.state = DELIVERED
    return job.deliverable


def approve_discrepancy(job: Job, disc_id: str) -> bool:
    for d in job.discrepancies:
        if d["id"] == disc_id and not d["resolved"]:
            d["resolved"] = True
            d["approved_value"] = d["pick_value"]
            cached = get_cached(job.account_id)
            if cached:
                for obj in cached[1].discrepancies:
                    if obj.id == disc_id:
                        obj.resolved = True
                        obj.approved_value = obj.pick_value
            return True
    return False


def all_resolved(job: Job) -> bool:
    return all(d["resolved"] for d in job.discrepancies)


async def run_book(on_update=None) -> list[Job]:
    r = httpx.get(f"{config.epic_url()}/expirations?days={config.LEAD_DAYS}", timeout=15)
    r.raise_for_status()
    accounts = r.json()["accounts"]
    jobs = []
    for acct in accounts:
        full = httpx.get(f"{config.epic_url()}/accounts/{acct['id']}", timeout=15).json()
        full["expires"] = acct["expires"]
        jobs.append(await run_account(full, on_update=on_update))
    return jobs
