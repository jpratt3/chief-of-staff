# Loss Run Agent

> The research layer of [CoS](../../README.md). Self-contained: everything here runs
> against mock carriers on a virtual clock, with no network and no real credentials.
> It has never touched a live carrier portal.

A working prototype of loss-run retrieval on autopilot: renewal calendar →
carrier portals and email → extraction → reconciliation → human review → dual
delivery. Built to test one claim — that a prose operating procedure, not
per-carrier scripts, can drive a browser agent across six different portals.

Everything runs locally against mock carriers. No real credentials, no network.

```bash
python run.py demo
```

---

## What it demonstrates

| Behaviour | Where |
|---|---|
| A prose SOP drives the agent — no per-carrier scripts | [`aops/loss_run_intake.md`](aops/loss_run_intake.md) |
| Self-correction: reads a format rule off the page and retries | Redstone portal, visible in the run trace |
| Refuses to guess: escalates `Input required` | Bluehaven portal (security question) |
| TOTP 2FA cleared from the vault | Northbridge portal |
| Takes *every* document offered, not just the first | Harborstone (loss run + large loss detail) |
| Email chase with auto follow-ups on day 3 and day 6 | Summit, Ironwood |
| Rejects a reply whose attachment is for the wrong policy | Ironwood, first reply |
| Six different PDF layouts into one schema | [`extract.py`](extract.py) |
| Reserve development annotated, **not** flagged | [`reconcile.py`](reconcile.py) |
| Discrepancies carry a proposed resolution + page citation | Review queue |
| Secrets never enter planner context or the trace | [`agent/tools.py`](agent/tools.py) |

The seeded book reconciles to a fixed target the tests assert against:
**47 claims · $412,655 paid · $538,190 incurred.**

---

## Running it

```bash
python run.py world      # start Applied Epic + 4 carrier portals
python run.py demo       # run the whole book, print the board
python run.py approve    # approve the 3 discrepancies and deliver
python run.py web        # review UI at http://127.0.0.1:8000
python run.py test       # 17 acceptance tests
python run.py reset      # wipe runs, mail, sharepoint, state
python run.py stop       # stop the mock world
```

First run copies `vault.example.json` to `vault.json` and generates the seed PDFs.

**Requirements**: Python 3.12+, `fastapi uvicorn pydantic reportlab openpyxl pypdf
pyotp httpx playwright anthropic pytest`, then `python -m playwright install chromium`.

### The demo, in order

1. **`/aop`** — the procedure. Point at it: *"this English document is the entire program."*
2. **`python run.py demo`** — the board fills. Portal carriers run concurrently while
   the email chase ticks on a virtual clock.
3. **Redstone's trace** — the agent submits `RS-221-0567`, the portal rejects it with
   an on-screen format rule, the agent reformats to `RS2210567` and retries.
   Nothing in the code knows about dashes.
4. **Bluehaven** — stops at `Input required`. It did not guess the security question.
5. **Review queue** — `GL-0088314`: $164,000 vs $158,500, each citing a PDF page.
   The agent picks; you confirm. Note the reserve-development case that was
   correctly *not* flagged.
6. **Delivery** — XLSX in `mock_sharepoint/Renewals/2026/Meridian/` and attached to
   the Applied Epic account, "Added by LossRunAgent".


---

## Architecture

```
run.py            CLI
web.py            review UI (board, tracker, run trace, review queue, AOP viewer)
orchestrator.py   job states, concurrency, delivery
  agent/
    executor.py     one policy: AOP + planner + tools + trace
    planners.py     ClaudePlanner (real) | ScriptedPlanner (offline)
    tools.py        browser/epic/email tools; secret resolution; tracing
    chase.py        email follow-up state machine on a virtual clock
    mailbox.py      maildir-style inbox/sent
  aops/             the Agent Operating Procedure -- the actual program
extract.py        PDF -> canonical schema (ClaudeExtractor | LayoutExtractor)
reconcile.py      conflicts, proposed resolutions, development notes
summary.py        the XLSX deliverable
schema.py         canonical claims model, every field carrying a citation
seed.py           deterministic book + six carrier PDF layouts
mock_epic.py      Applied Epic (expiration report, attachments, activities)
mock_portals.py   four carrier portals, each with a different obstacle
```

### The AOP is the program

`aops/loss_run_intake.md` is prose. At run time `executor.render_aop()` substitutes
`data.*` chips from the renewal record and hands the result to the planner as its
system prompt. `auth.*` chips are **not** substituted — they stay as literal text.
The model emits `browser_fill(selector="#password", secret_ref="auth.Redstone.password")`
and the tool layer resolves it from `vault.json`. The secret never enters model
context and never reaches `trace.jsonl`. `test_no_secret_ever_reaches_a_trace`
greps every trace to prove it.

### Two planners

`ClaudePlanner` is the real one: AOP as a cached system prompt, Claude tool-use loop.
It runs whenever `ANTHROPIC_API_KEY` is set.

`ScriptedPlanner` is a deterministic stand-in so the pipeline runs and tests offline.
It is **reactive, not hardcoded** — it only reformats the policy number *after*
reading the rejection text off the page, and only escalates *after* reading the
security prompt. The behaviour being demonstrated is genuine under both.

```bash
LOSSRUN_PLANNER=claude python run.py demo    # force the real planner
LOSSRUN_PLANNER=scripted python run.py demo  # force the offline one
```

Same for extraction: `ClaudeExtractor` hands the PDF to the model and validates
strict JSON with one self-repair round-trip; `LayoutExtractor` is a generic
header-alias table parser that handles all six layouts. It parses the PDF text —
it does not read the seed data.

### Swapping in computer use

The browser tools are deliberately generic (`goto`, `read_page`, `fill`, `click`,
`select`, `download`). A computer-use executor implementing the same six names
against screenshots instead of the DOM would run the identical AOP. That is the
portability claim, and it is the one that matters in practice — insurance back
offices run on Citrix and green-screen terminals where there is no DOM to read.

---

## The mock world

| Service | Port | Obstacle |
|---|---|---|
| Applied Epic | 8100 | — |
| Northbridge | 8201 | username/password + TOTP 2FA, async report generation |
| Harborstone | 8202 | serves **two** documents; the AOP requires taking both |
| Bluehaven | 8203 | security question with no answer in the vault |
| Redstone | 8204 | rejects policy numbers containing separators |
| Summit | email | replies after 1 follow-up, with two valuations |
| Ironwood | email | replies after 2 follow-ups; first attachment is the wrong policy |

Meridian Logistics carries 12 policies across these 6 carriers. Eleven are
retrievable; Bluehaven's escalates. That is why the tracker reads **11 of 12** —
and **8 of 12** mid-run.

### Seeded reconciliation cases

Three true discrepancies and one trap:

1. **Amount** — `GL-0088314` is $164,000 on the carrier run (p.4) and $158,500 on
   the large loss detail (p.1). The canonical amount-conflict case.
2. **Missing claim** — `GL-0089930` appears only on the supplemental report.
3. **Date of loss** — `GL-2799884` differs by three days between the two documents.
4. **Not a discrepancy** — `AU-8002462` develops $18,200 → $41,700 between the
   2025 and 2026 valuations. Normal reserve strengthening. Annotated, never flagged.

That fourth case is the one that matters. A system that flags it is unusable,
because every renewal has dozens of them.

---

## Tests

```bash
python run.py test    # 17 passed in ~35s
```

They assert the acceptance criteria, not implementation details: exact hero-card
totals, every layout extracting, exactly three discrepancies with zero false
positives, the Redstone rejection being *observed* before the retry, Bluehaven
never filling the security answer, Harborstone yielding both documents, TOTP being
used, the workbook totals, the chase cadence landing on days 3 and 6, and no
secret in any trace.

---

## Known limits

- The non-Meridian accounts on the board are presentation-only, so the expiration
  report reads like the reference screenshot. Only Meridian runs end to end.
- `run.py approve` re-runs the book when the reconciliation cache is cold, because
  that cache is in-process. Approving in the web UI is instant.
- `ClaudeExtractor` and `ClaudePlanner` are written and wired but have not been
  executed — no API key was available in the build environment. The offline paths
  are what the 17 tests exercise.
- Portal downloads are fetched through Playwright's request context rather than
  the browser download manager. Simpler, and it keeps the bytes in the run folder.
