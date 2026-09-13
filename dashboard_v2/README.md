# `dashboard_v2/` — the web UI

**Status: Mixed.** Three skills are real adapters onto [`engine/`](../engine/README.md).
Eight are deterministic scaffolding. The table below says which is which.

Flask app on port 8000. It is the only UI — an earlier stage-only dashboard was
removed once the engine moved into its own package.

> The directory keeps the `_v2` name for now because renaming it would break
> muscle memory and launcher scripts for no functional gain. There is no v1.

---

## The model

An earlier version navigated by **stage** only: open Renewal Preparation, see its
tasks. That works until you ask a question that runs the other way — *"where does
the Loss Run request show up across the whole renewal?"* It appears in Renewal Prep
and again in Submission, and a stage-only view has no way to express that.

This one adds a **portal axis**. Stages are rows, portals are columns, each cell is
the skill at that intersection.

| Portal | Active stages | Purpose |
|---|---|---|
| Renewal Pipeline | Prep, RSM, Submission, Proposal | Document and communication flow, exposure collection → marketplace send |
| Deck Builder | Prep, RSM, Proposal | Presentation assembly from templates and portal data |
| Document Review | Prep, Submission, Proposal, Bind, Post Binding | Upload-and-compare; surfaces discrepancies for a human |
| Document Generator | all six | Outbound drafts produced from portal/config data |
| System Updates | Prep, Proposal, Bind, Post Binding | Updates to external systems (AMS, the certificate system, DMS) |
| Meeting Scheduler | Prep, Post Binding | Calendar-integrated scheduling with invite generation |
| Invoicing Assistant | Proposal, Invoice, Post Binding | Per-coverage invoicing state; policy receipt follow-up |

Navigate by stage (row) to see what's due; by portal (column) to follow one thread
across the whole renewal.

---

## Skills

Each skill is a Flask Blueprint + a `service.py` + a Jinja template, living at
`/skills/<name>` and reachable from the task row that triggers it.
`config/skill_map.json` maps stage → task number → skill URL, so task renumbering
is a config edit rather than a template edit.

| Skill | LOC | Status | Notes |
|---|---:|---|---|
| `invoicing-assistant` | 987 | **Live** | Premium, commission rate, taxes/fees, total billed. Own [eval](skills/invoicing-assistant/eval/run_eval.py): **60/60 = 100%** over 15 hand-labelled binders. `money.py` is the extraction core; identity fields come from the shared engine. |
| `loss-run` | 36 | **Live** | Adapter onto `engine.loss_run` (99.5% / 80 binders). |
| `rsm` | 120 | **Live** | Adapter onto `engine.rsm` — ZIP-level cross-presentation slide assembly. |
| `ecp-reviewer` | 40 | Scaffolded | Generates the ECP form review checklist. Does not yet read the form. |
| `exposure-request` | 50 | Scaffolded | Drafts the exposure-data request. Recipients entered by hand. |
| `header-updates` | 24 | Scaffolded | Emits AMS submission-header update steps. No AMS integration. |
| `home-state-assigner` | 38 | Scaffolded | Home-state validation checklist. No document parsing. |
| `strategy-deck` | 43 | Scaffolded | Rewrites year strings inside one deck. Real assembly is the `rsm` skill. |
| `strategy-scheduler` | 52 | Scaffolded | Meeting details and agenda text. No calendar write. |
| `renewal-kickoff` | 38 | Scaffolded | Drafts the kickoff email from config. |
| `team-confirmer` | 45 | Scaffolded | Team-assignment confirmation checklist. No AMS write. |

**What "scaffolded" means here:** the skill runs, is wired into the matrix, and
returns correct deterministic output — a checklist, a draft, a set of steps. What
it does not do is talk to the external system it describes (AMS, the certificate
system, DMS) or parse the document it references. They are the UI and the domain
logic for integrations that do not exist yet, not mockups.

---

## Adapters stay thin

Three skills read binders. **None of them owns a parser.** Each holds a short
adapter that reshapes engine output for its templates:

```python
from engine import loss_run as _engine
process_batch = _engine.process_batch
```

That is the whole dependency. A rule added in an adapter is a rule
[the eval](../engine/README.md#the-eval) does not measure, so extraction logic
belongs in `engine/` or nowhere.

`app.py` puts the repo root on `sys.path` so `engine` imports normally. An earlier
arrangement loaded it through a file-path loader with hand-seeded `sys.modules`
entries, because the engine lived inside another app's Blueprint package. Moving
the engine out deleted about 80 lines of that across two skills.

---

## Architecture decisions

Recorded with rejected alternatives in
[`docs/Skills/skills.md`](../docs/Skills/skills.md). Short version:

**Blueprints, not a second process.** Each skill is self-contained — own routes,
templates, logic — but runs in one process. Adding a skill touches no existing
code; removing one is a folder delete plus a line in `app.py`.

**Service layer is Flask-free.** Logic in `service.py` with no Flask imports; route
handlers are thin adapters. Keeps skills independently testable.

**Shared result contract.** Engine entry points return `SkillResult` — `success`,
`data`, `error`. Error handling lives in one place.

**Single-user, local-only.** No auth, no sessions. `debug=True, use_reloader=False`:
templates reload on save without restarting the server. Built decks are held in a
module-level dict until downloaded, which is the right amount of machinery for one
user on one machine.

---

## Structure

```
app.py                  Flask entry point, port 8000
data_loader.py          read-only access to workbook + config + data/
templates/
  overview.html         the matrix
  stage.html            row view — tasks in one stage
  portal.html           column view — one portal across stages
skills/<name>/
  __init__.py           Blueprint
  service.py            logic / adapter, no Flask imports
  templates/<name>/
```

Skill directories are hyphenated (`home-state-assigner`), which is not a legal
module name — `app.py` loads each by file path via `importlib.util`.

## Running

```bash
.venv/Scripts/python.exe dashboard_v2/app.py
```

Or `run_dashboard_v2.bat`. Needs `config/clients.json` and `config/settings.json` —
copy the `.example` files and fill them in.
