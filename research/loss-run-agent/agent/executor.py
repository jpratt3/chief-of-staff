"""Runs one policy retrieval: AOP + planner + tools, with a full trace."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from agent import mailbox
from agent.planners import Action, ClaudePlanner, ScriptedPlanner
from agent.tools import BrowserTools, Trace, vault_has

AOP_PATH = config.ROOT / "aops" / "loss_run_intake.md"
MAX_STEPS = 60


@dataclass
class RunResult:
    run_id: str
    policy_number: str
    carrier: str
    status: str
    summary: str
    artifacts: list[str] = field(default_factory=list)
    steps: int = 0
    run_dir: Path | None = None


def build_context(account: dict, policy: dict) -> dict:
    import seed
    carrier = policy["carrier"]
    meta = config.CARRIERS[carrier]
    return {
        "account": account["account"],
        "account_id": account["id"],
        "policy_number": policy["policy_number"],
        "carrier": carrier,
        "line": policy["line"],
        "channel": meta["channel"],
        "period_start": seed.PERIOD_START.strftime("%m/%d/%Y"),
        "period_end": seed.PERIOD_END.strftime("%m/%d/%Y"),
        "expires_short": datetime.fromisoformat(account["expires"]).strftime("%m/%d"),
        "portal_url": config.base_url(carrier) if meta["channel"] == "portal" else "",
        "carrier_email": meta.get("email", ""),
    }


def render_aop(ctx: dict) -> str:
    """Resolve data.* chips. auth.* chips stay as literal text -- the tool layer
    resolves those, so secrets never enter planner context."""
    text = AOP_PATH.read_text(encoding="utf-8")
    mapping = {
        "data.Renewal.account": ctx["account"],
        "data.Renewal.policy_number": ctx["policy_number"],
        "data.Renewal.carrier": ctx["carrier"],
        "data.Renewal.line": ctx["line"],
        "data.Renewal.channel": ctx["channel"],
        "data.Renewal.period_start": ctx["period_start"],
        "data.Renewal.period_end": ctx["period_end"],
        "data.Renewal.expires_short": ctx["expires_short"],
        "data.Renewal.portal_url": ctx["portal_url"],
        "data.Renewal.carrier_email": ctx["carrier_email"],
        "data.Broker.name": config.BROKER_NAME,
    }
    for k, v in mapping.items():
        text = text.replace("{" + k + "}", str(v))
    text = text.replace("<Carrier>", ctx["carrier"])
    return text


def new_run_dir(policy_number: str) -> tuple[str, Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    run_id = f"{re.sub(r'[^A-Za-z0-9]', '', policy_number)}-{stamp}"
    d = config.RUNS_DIR / run_id
    (d / "artifacts").mkdir(parents=True, exist_ok=True)
    return run_id, d


async def run_policy(account: dict, policy: dict, headless: bool = True,
                     force_planner: str | None = None) -> RunResult:
    ctx = build_context(account, policy)
    run_id, run_dir = new_run_dir(ctx["policy_number"])
    trace = Trace(run_dir=run_dir)
    aop = render_aop(ctx)
    (run_dir / "aop.md").write_text(aop, encoding="utf-8")
    (run_dir / "context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")

    use_claude = (force_planner == "claude") or (force_planner is None and config.use_claude())
    planner_name = "claude" if use_claude else "scripted"
    trace.step = "1. Decide the channel"

    result = RunResult(run_id=run_id, policy_number=ctx["policy_number"],
                       carrier=ctx["carrier"], status="failed",
                       summary="did not start", run_dir=run_dir)

    # Email carriers never open a browser.
    if ctx["channel"] == "email":
        planner = ScriptedPlanner(ctx) if not use_claude else ClaudePlanner(ctx, aop)
        return await _email_loop(ctx, planner, trace, result, use_claude)

    async with BrowserTools(trace, headless=headless) as browser:
        async def dispatch(a: Action) -> str:
            n, inp = a.name, a.input
            if n == "browser_goto":
                trace.step = "2. Sign in to the carrier portal"
                return await browser.goto(inp["url"])
            if n == "browser_read_page":
                return await browser.read_page()
            if n == "browser_fill":
                ref = inp.get("secret_ref")
                if ref and not vault_has(ref):
                    return (f"NOT AVAILABLE: {ref} is not in the vault. "
                            f"Per the CRITICAL guardrail, do not guess -- escalate.")
                return await browser.fill(inp["selector"], inp.get("value"), ref)
            if n == "browser_click":
                return await browser.click(inp["selector"])
            if n == "browser_select":
                trace.step = "4. Generate the loss run"
                return await browser.select(inp["selector"], inp["value"])
            if n == "browser_download":
                trace.step = "5. Download every document on offer"
                out = await browser.download(inp["selector"])
                m = re.search(r"Downloaded (\S+)", out)
                if m:
                    result.artifacts.append(m.group(1))
                return out
            return f"Unknown tool {n}"

        if use_claude:
            planner = ClaudePlanner(ctx, aop)
            for step in range(MAX_STEPS):
                calls, _text = planner.next_batch()
                if not calls:
                    result.status, result.summary = "failed", "planner stopped without finishing"
                    break
                finished = False
                for tool_id, action in calls:
                    if action.name == "done":
                        result.status = action.input.get("status", "failed")
                        result.summary = action.input.get("summary", "")
                        trace.record("done", action.input, result.status)
                        finished = True
                        break
                    obs = await dispatch(action)
                    planner.observe(tool_id, obs)
                result.steps = step + 1
                if finished:
                    break
        else:
            planner = ScriptedPlanner(ctx)
            obs = ""
            for step in range(MAX_STEPS):
                action = planner.next(obs)
                if action.name == "done":
                    result.status = action.input["status"]
                    result.summary = action.input["summary"]
                    trace.record("done", action.input, result.status)
                    result.steps = step + 1
                    break
                obs = await dispatch(action)
                result.steps = step + 1

    _write_result(run_dir, result, planner_name, ctx)
    return result


async def _email_loop(ctx, planner, trace, result, use_claude) -> RunResult:
    trace.step = "6. Request by email"
    if use_claude:
        for _ in range(6):
            calls, _t = planner.next_batch()
            if not calls:
                break
            done = False
            for tool_id, action in calls:
                if action.name == "done":
                    result.status = action.input.get("status", "awaiting_reply")
                    result.summary = action.input.get("summary", "")
                    done = True
                    break
                if action.name == "email_send":
                    mailbox.send(action.input["to"], action.input["subject"],
                                 action.input["body"], ctx["policy_number"])
                    trace.record("email_send", {"to": action.input["to"],
                                                "subject": action.input["subject"]},
                                 "request sent")
                    planner.observe(tool_id, "Sent.")
                else:
                    planner.observe(tool_id, f"Tool {action.name} not available on this path.")
            if done:
                break
    else:
        obs = ""
        for _ in range(6):
            action = planner.next(obs)
            if action.name == "done":
                result.status = action.input["status"]
                result.summary = action.input["summary"]
                break
            if action.name == "email_send":
                mailbox.send(action.input["to"], action.input["subject"],
                             action.input["body"], ctx["policy_number"])
                trace.record("email_send", {"to": action.input["to"],
                                            "subject": action.input["subject"]},
                             "request sent")
                obs = "Sent."
    trace.record("done", {"status": result.status}, result.status)
    _write_result(result.run_dir, result, "claude" if use_claude else "scripted", ctx)
    return result


def _write_result(run_dir: Path, result: RunResult, planner: str, ctx: dict) -> None:
    (run_dir / "result.json").write_text(json.dumps({
        "run_id": result.run_id,
        "policy_number": result.policy_number,
        "carrier": result.carrier,
        "account": ctx["account"],
        "status": result.status,
        "summary": result.summary,
        "artifacts": result.artifacts,
        "steps": result.steps,
        "planner": planner,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
