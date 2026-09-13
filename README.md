# Chief of Staff

A renewal-operations workflow engine for a commercial property & casualty insurance
book. A commercial insurance renewal is a 90-180 day project that repeats every year,
per client, per coverage line, and most of the work is document logistics: collect
exposure data, request loss runs from every incumbent carrier, assemble a submission,
compare quotes, bind, invoice. The tracking for that work normally lives in a
spreadsheet and a mailbox, which means status is whatever someone last remembered to
type. This project replaces that with a pipeline: read mail and calendar, infer which
renewal stage each client is in, derive work-item status from evidence rather than
self-report, and expose task-linked tools that do the repetitive document work — reading
binders, drafting carrier requests, assembling decks, reconciling invoice figures.

It is a personal portfolio project, built to demonstrate systems engineering against a
domain the author knows well. It is not affiliated with, and does not describe the
internal process of, any employer.

---

## Architecture

Four layers, deliberately decoupled.

**Ingestion and inference (`src/`).** Readers pull mail and calendar items from a local
desktop Outlook profile over COM (`pywin32`) rather than Graph — no app registration, no
token cache, no client secret. `client_resolver.py` maps a message to a client and
engagement using folder placement plus configured aliases. `inference_engine.py` scores
each item against stage keyword lists and produces stage signals.
`status_logic.py` is the single place that decides what a work item's status is; nothing
else in the tree is allowed to make that call. `workbook_writes.py` projects the result
into a tracker workbook, and `briefing_builder.py` renders a daily summary.

**The 7-stage pipeline.** Every engagement moves through a fixed stage order:

| # | Stage | What happens |
|---|---|---|
| 1 | Renewal Preparation | Kickoff, team confirmation, exposure collection, loss-run requests |
| 2 | RSM | Renewal strategy meeting; placement strategy is agreed and recorded |
| 3 | Submission | Submission assembled and sent to the marketplace |
| 4 | Proposal | Quotes returned, compared, and presented |
| 5 | Bind | Bind order issued; binders received and checked |
| 6 | Invoice | Premium, commission, taxes and fees reconciled and invoiced |
| 7 | Post Binding | Subjectivities closed, policies received and reviewed, documents issued |

Stages are the row axis of the dashboard. Each stage carries a numbered task list, and a
task may have a tool attached to it.

**Document extraction and assembly (`engine/`).** UI-independent and Flask-free, so it
can be graded outside a request context and reused by more than one caller. The loss-run
extractor reads a carrier binder in whatever layout the carrier chose — there is no
schema, and a binder from one carrier looks nothing like a binder from the next. It
ranks pages to find the declarations page, then runs successive passes (label:value
scan, dense-text splitter, `pdfplumber` spatial layout) and scores the candidates each
pass produces for insured, carrier, policy number, coverage line and effective date.
Coverage strings resolve to canonical categories through `config/coverage_aliases.json`
rather than hardcoded rules. The deck assembler rewrites PowerPoint files at the ZIP
level — renaming parts, rewriting relationship targets, patching
`[Content_Types].xml` — because the `python-pptx` cross-presentation copy API produces
files PowerPoint will not open without repair.

**Web application (`dashboard_v2/`).** A Flask app serving a stage x portal matrix:
stages are rows, functional portals (Document Review, Deck Builder, Document Generator,
System Updates, Meeting Scheduler, Invoicing Assistant, Renewal Pipeline) are columns,
and each cell is a skill. A "skill" is this project's name for a task-linked tool page,
not a framework concept. Each one is a directory containing a Flask Blueprint, a
Flask-free `service.py` holding the business logic, and Jinja2 templates. Registration
is a plugin pattern with three touch points and no other file needs to know the skill
exists:

1. `dashboard_v2/app.py` loads each skill directory by name and registers the `bp`
   Blueprint it exposes; a failed load is logged and skipped rather than taking down
   the app
2. the route lives at `/skills/<name>`
3. `config/skill_map.json` maps stage name -> task number -> skill URL, so renumbering a
   stage's tasks is a config edit rather than a template change

Every service function returns the same `SkillResult` shape (`success`, `data`,
`error`), which keeps error handling and logging in one base class.

**Eval harness (`engine/eval/`).** `run_eval.py` grades the extractor cell by cell
against hand-checked truth files. Ground truth for a field is a *list* of acceptable
answers, not a single string, because some questions are genuinely ambiguous — on an
excess tower, the fronting carrier and the syndicate are both defensible answers to
"who is the carrier". The harness exists so that accuracy claims are reproducible and so
that regressions are visible; a companion script prints, for a single document, which
pages were selected and why, every candidate each field generated, and the score that
picked the winner.

**Research (`research/loss-run-agent/`).** A self-contained agentic replica of
carrier-portal loss-run retrieval: fictional carrier portals, several document layouts,
a virtual clock, no network and no real credentials. The agent reads a prose operating
procedure rather than per-carrier scripts, and stops with an explicit "input required"
when a portal asks for something it does not hold instead of guessing. Secrets are never
placed in planner context — the agent emits a `secret_ref` and the resolver substitutes
at the tool boundary.

---

## Project structure

```
config/           Stage keywords, coverage aliases, skill map, example client config
data/             Example carrier routing table
docs/Skills/      Architecture decision record, coverage vocabulary, carrier name map
engine/           Document extraction and deck assembly; no Flask imports
  eval/           Accuracy harness and truth files
dashboard_v2/     Flask app, templates, static assets
  skills/         One directory per task-linked tool
research/         Loss-run agent replica (own requirements.txt and tests)
src/              Outlook readers, inference engine, status logic, workbook writes
.env.example      Environment template for the src/ pipeline
```

---

## Setup

Requires Python 3.12+. The `src/` pipeline additionally requires Windows with a desktop
Outlook profile, since it uses COM rather than a cloud API; the dashboard and the
extraction engine do not.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

Run the dashboard:

```bash
.venv/Scripts/python.exe dashboard_v2/app.py
```

It serves on port 8000. `run_dashboard_v2.bat` does the same thing, and
`.claude/launch.json` defines the same entry point for editor-launched runs. The app
treats the workbook and config files as read-only inputs and never writes to them.

`research/loss-run-agent/` is a separate subproject with its own `requirements.txt`;
install that separately if you want to run it.

---

## Configuration

Anything that would hold a real client book is gitignored, and a committed `.example`
companion documents the schema:

| Committed example | Gitignored real file | Holds |
|---|---|---|
| `config/clients.example.json` | `config/clients.json` | Client roster, aliases, engagements, per-stage dates, billing identifiers, team assignments |
| `config/settings.example.json` | `config/settings.json` | Mail/calendar lookback windows, the mail folders to read, firm identity |
| `data/loss_run_carriers.example.json` | `data/loss_run_carriers.json` | Carrier group -> sub-company -> loss-run contact or portal |
| `.env.example` | `.env` | Filesystem paths, user email, timezone |

Copy each example to its real name and fill it in:

```bash
cp config/clients.example.json config/clients.json
cp config/settings.example.json config/settings.json
cp data/loss_run_carriers.example.json data/loss_run_carriers.json
```

`config/stage_keywords.json`, `config/coverage_aliases.json` and
`config/skill_map.json` carry no client data and are committed as-is.

---

## Data

All data committed to this repository is synthetic. The clients, contacts, email
addresses and billing identifiers in the example configs are invented, and the documents
the extraction engine reads are not included. Real carrier and product names appear in
`docs/Skills/companymap.md` and `config/coverage_aliases.json` — those are public
companies referenced as integration targets, not client information.

---

## Maturity

This is a working personal prototype, not a product. Stated plainly:

- **The `src/` pipeline has no entry point in this tree.** The reader, inference, status
  and workbook modules are complete and internally consistent, but the orchestrator that
  called them daily is not included. Treat `src/` as built and documented, not as
  currently runnable.
- **Most dashboard skills are scaffolding.** Three are real adapters onto the extraction
  engine. The rest generate correct checklists and draft text deterministically but do
  not yet talk to the external systems they describe.
  `dashboard_v2/README.md` marks each one.
- **The eval cannot be reproduced from a clone.** The harness, the truth-file format and
  the scoring logic are here; the source documents it grades are not, because they are
  not mine to publish.
- **The loss-run agent is a replica.** It has only ever run against mock portals on a
  virtual clock.
- **Single user, local only.** No authentication, no sessions, no multi-tenancy. It
  assumes one person on one machine.
