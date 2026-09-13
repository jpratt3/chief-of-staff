# Skills — Architecture & Decision Record
*Dashboard v2 portal design | Last updated from portal_matrix.xlsx*

---

## What Skills Are

Skills are interactive tool pages embedded in the CoS dashboard. Each skill is a task-linked UI that automates a specific, repeatable workflow step — file upload, data extraction, email drafting, deck assembly, system update, etc. They live at `/skills/<name>` and are reachable directly from the task row that triggered them.

The term "skill" is a project-level name, not a framework or library. Under the hood, each skill is a Flask Blueprint + a service module + a Jinja2 template.

**Skills currently live:**
- Loss Run Request (`/skills/loss-run`) — Renewal Preparation task #9; also covers Submission task #2
- RSM Deck Builder (`/skills/rsm`) — RSM task #2

---

## Dashboard v2 — Portal Architecture

Dashboard v2 runs on **port 8000**. Dashboard v1 runs on **port 5000** and must remain completely untouched.

> **HARD RULE — NO EXCEPTIONS:** Dashboard v2 must not modify, import from, or overwrite any file that belongs to Dashboard v1. This includes `dashboard/app.py`, `dashboard/data_loader.py`, `dashboard/templates/`, `dashboard/static/`, `dashboard/skills/`, and every file under `src/`, `config/`, `data/`, and `workbook/` that the v1 pipeline owns. v2 is a fully separate application in a fully separate directory. If a build agent touches a v1 file for any reason, that is a bug — stop and reverse it immediately.

v2 lives in its own directory (`dashboard_v2/`) with its own `app.py`, templates, static assets, and skills. It may read from the same source data (`config/clients.json`, `workbook/chief_of_staff_tracker.xlsx`, `data/` JSON files) as **read-only** inputs — it never writes to them. The v1 pipeline (`run_daily.py` and everything it calls) is never modified, never called by v2, and never aware v2 exists.

The fundamental shift from v1 to v2 is the addition of a **portal axis**. v1 navigates by stage row only — open a stage, see its tasks. v2 adds dual navigation:

- **By stage (row)** — same as v1; open a stage, see all tasks and their portal entry points
- **By portal (column)** — open a portal, see its thread across every stage where it's active

This is the matrix model: stages are horizontal rows, portals are vertical columns, and each cell is a skill or set of skills at that intersection. The sources of truth for this matrix are:
- `docs/Skills/portal_matrix.xlsx` — Spec sheet: skill specs by stage/portal intersection
- `docs/Skills/skills_planning.xlsx` — full task-level detail: skill names, inputs, outputs, and notes per stage task

---

## Portal Columns

Seven portal columns span the renewal cycle.

| Portal | Active Stages | Primary Purpose |
|---|---|---|
| **Renewal Pipeline** | Renewal Prep, RSM, Submission, Proposal | Central document and communication flow from exposure collection through marketplace send |
| **Deck Builder** | Renewal Prep, RSM, Proposal | Presentation deck assembly from templates and portal data |
| **Document Review** | Renewal Prep, Submission, Proposal, Bind, Post Binding | Upload-and-compare workflows; surfaces discrepancies for human review |
| **Document Generator** | Renewal Prep, RSM, Submission, Proposal, Bind, Post Binding | Outbound email drafts and documents produced from portal/config data |
| **System Updates** | Renewal Prep, Proposal, Bind, Post Binding | Automates updates to external systems (AMS, the certificate system, DMS) |
| **Meeting Scheduler** | Renewal Prep, Post Binding | Calendar-integrated meeting scheduling with invite generation |
| **Invoicing Assistant** | Proposal, Invoice, Post Binding | Invoicing stage tracking per coverage line; policy receipt follow-up |

---

## Architecture Decisions

### Decision 1: Flask Blueprints, not a second process

| Option | Description | Verdict |
|---|---|---|
| A — Routes in `app.py` | Add skill routes directly to the main app | Rejected: doesn't scale |
| B — Flask Blueprints | Each skill is a self-contained Blueprint | **Chosen** |
| C — Separate Flask process | Skills on a second port | Deferred: viable later |
| D — JS modal | Skill UI as overlay on same page | Rejected for multi-step flows |

Each skill is fully isolated — its own routes, templates, and logic — but runs in the same process. Adding a skill never touches existing code. Removing one is a folder delete and a single line in `skills/__init__.py`. Migrating to a separate process later requires moving a folder and changing one URL.

### Decision 2: Service layer is Flask-free

Each skill has a `service.py` with all business logic and **no Flask imports**. Route handlers are thin adapters only. This keeps every skill independently testable and makes process extraction a one-afternoon job.

### Decision 3: Task-to-skill mapping lives in `config/skill_map.json`

Maps stage name → task number → skill URL. Task numbers shifting in `Renewal_Timeline.xlsx` only requires updating the config, not the template.

### Decision 4: Shared result contract (`SkillResult`)

All service functions return a `SkillResult` — `success: bool`, `data: dict`, `error: str`. Consistent shape across all skills; error handling and logging touch one base class, not every skill.

### Decision 5: Single-user, local-only — no auth, no sessions

Personal productivity tool on a local machine. `debug=True, use_reloader=False` — templates reload on save, no server restart needed.

### Decision 6: Portal navigation added in v2

Portals are registered as Flask Blueprints at `/portal/<name>`. Each portal view reads across all stages for its column. Stage views are unchanged — they include portal launch buttons per task row in addition to existing skill buttons.

### Decision 7: v1 (port 5000) stays intact; v2 runs on port 8000

No changes to the existing dashboard, pipeline, or any v1 source files. v2 is built entirely in `dashboard_v2/`. A build agent that modifies any v1 file has made an error regardless of the reason.

---

## Skill Registry Pattern

When a new skill is built, three things are registered:

**1. `config/skill_map.json`:**
```json
{
  "Stage Name": {
    "task_number": "/skills/skill-name"
  }
}
```

**2. `dashboard/skills/__init__.py`:**
```python
from .skill_name import bp as skill_name_bp
app.register_blueprint(skill_name_bp)
```

**3. `dashboard/skills/<name>/__init__.py`:**
```python
bp = Blueprint("skill_name", __name__, template_folder="templates")

@bp.route("/skills/skill-name", methods=["GET", "POST"])
def index():
    ...
```

No other files need to know a new skill exists.

---

## Live Skills

| Stage | Task | Skill | URL | Portal |
|---|---|---|---|---|
| Renewal Preparation | #9 | Loss Run Request | `/skills/loss-run` | Document Review |
| Submission | #2 | Loss Run Request | `/skills/loss-run` | Document Review |
| RSM | #2 | RSM Deck Builder | `/skills/rsm` | Deck Builder |

---

## Portal Specs

---

### Renewal Pipeline

**Purpose:** Central document and communication flow from exposure collection through marketplace send. The shared workspace that spans the widest slice of the renewal — everything from the first exposure request to the final marketplace submission. Placement team and PDM have access. AE can annotate. All references stored in one place so downstream stages (Proposal, Bind) can pull from it without re-uploading.

**Active stages:** Renewal Prep, RSM, Submission, Proposal

**Design note:** Highest-priority portal to build. The portal question flagged in the matrix — whether placement/client team can interact with it similarly to a shared Teams channel — should be decided before building. Auth-gated access for placement/PDM is the target model.

| Stage | Skill | Task Ref | Trigger | Input | Output | Owner |
|---|---|---|---|---|---|---|
| Renewal Prep | Exposure Request | #10 + #11 | Kickoff complete | Prior year Excels/PDFs; config (placement team) | Client exposure checklist; updated spreadsheets with new columns/years; emails to placement confirming apps/subjectivities needed | AAE |
| RSM | Certificate List | #6 | Auto-prompt X days pre-renewal | Stored cert list | Draft email to client with expiring cert list | AR |
| RSM | Flood Zone Determinations | #7 | Property policies identified | Property policies | Email draft to team re: flood zone determinations needed | AAE / AR |
| Submission | Submission Reviewer + Submission Sender | #1, #4, #6, #8 | Exposures received from client | Client exposures; PSL; submission docs; config | Variance analysis; draft submission; emails to AE / placement / marketplace; quote due date backdated from renewal timeline | AAE / AR |
| Proposal | Submission Q&A | #2 | UW questions received | UW questions; submission portal data | Auto-drafted UW responses from portal data | AAE / AR |

**Notes:**
- Certificate List (RSM #6) requires a persistent cert data store in the dashboard — accessible to all team members, updated year over year
- Flood Zone Determinations is as-needed; triggered only when property policies are present
- Submission Reviewer and Submission Sender are a single portal view — Reviewer surfaces variances, Sender produces the email chain
- For public companies, exposure spreadsheet prefill from public data is an open question

---

### Deck Builder

**Purpose:** Assembles presentation decks at key renewal stages from templates and portal data. RSM Deck Builder is the live proof-of-concept. The Strategy Deck Builder and Proposal Builder follow the same ZIP-level assembly pattern.

**Active stages:** Renewal Prep, RSM, Proposal

**service.py approach (all three skills):** ZIP-level slide assembly — open source and destination as ZipFile objects, rename parts to avoid collisions, copy all directly-referenced parts, rewrite rId targets, add slide to Content_Types.xml and presentation.xml. Do not use python-pptx cross-presentation copy API (broken).

| Stage | Skill | Task Ref | Trigger | Input | Output | Owner |
|---|---|---|---|---|---|---|
| Renewal Prep | Strategy Deck Builder | #7 | Internal Strategy Meeting (ISM) template needed | Last year's ISM deck | Prefilled ISM template to fine-tune | AAE |
| RSM | RSM Deck Builder | #2 | RSM stage begins | Last year's deck; program graphic (optional); market slides | Assembled RSM deck (.pptx) | AR |
| Proposal | Proposal Builder | #7, #10 | Quotes finalized; proposal stage | Last year's proposal; this year's quotes; portal data | Prefilled proposal deck | AR |

**Build order:** Strategy Deck Builder first (simpler — no market slide catalog, no program graphic). Proposal Builder second (same pattern as RSM, adds quote data input from Renewal Pipeline portal).

**Dependencies:** `lxml` (XML manipulation), `python-pptx` (slide inventory only — not for copy)

---

### Document Review

**Purpose:** Upload-and-compare workflows where a document is reviewed against a standard, prior version, or marketing intent. Loss Run Request is the live proof-of-concept — the same upload → extract → review → draft pattern applies to ECP forms, quotes, binders, and policies.

**Active stages:** Renewal Prep, Submission, Proposal, Bind, Post Binding

**Pattern (inherited from Loss Run):** File upload → extraction pass → editable review table → output (email draft, diff report, or portal update). "Could not extract" is always a UI state, never a crash — return empty fields for manual entry.

| Stage | Skill | Task Ref | Trigger | Input | Output | Owner |
|---|---|---|---|---|---|---|
| Renewal Prep | ECP Reviewer | #3 | Surplus lines carrier detected on account | Prior year ECP form (upload) | Exposure request email bullet confirming ECP status | AR |
| Renewal Prep | Home State Assigner | #4 | Home state review needed | Policies/invoices | Drafted email to placement rep based on findings or precedent | AR |
| Submission | Loss Run Request | #2 | New valuation date needed | Binder files (PDF/DOCX/XLSX) | Updated loss summary; grouped email drafts to carriers | AR |
| Proposal | Quote Reviewer | #1, #5 | Quotes received from markets | Quotes; submission data | Cost & Coverage Comparison (CCC) style diff of quotes vs. submission; drafted email to PDM | AR |
| Bind | Binder Reviewer | #6, #8 | Binders received | Binders | Diff from marketing intent; auto-drafted correction email; binding portal update | AR |
| Post Binding | Policy Checker | #7, #8 | Policies received (30 days post-bind) | Policies; binders; proposal data | Accuracy diff report; submitted to AE | AR |

**Notes:**
- Loss Run Request (Submission #2) is the same skill as Renewal Prep #9 — no separate build needed
- Quote Reviewer: an existing team tool may overlap — confirm before building. PDM should have portal access
- Policy Checker: an equivalent is being built elsewhere — do not duplicate. Reference externally
- Home State Assigner moved from Document Generator to Document Review — it reviews policy/invoice documents before producing an output

---

### Document Generator

**Purpose:** Produces outbound email drafts and documents from portal/config data. No file upload or comparison — inputs are structured data already in the system (config, portal data, meeting notes). The widest horizontal thread: active in every stage except Invoice.

**Active stages:** Renewal Prep, RSM, Submission, Proposal, Bind, Post Binding

| Stage | Skill | Task Ref | Trigger | Input | Output | Owner |
|---|---|---|---|---|---|---|
| Renewal Prep | Renewal Kickoff | #5 | Renewal initiated | Config (team members) | Draft kickoff email to client team | AE / AAE / AR |
| RSM | PSL Builder | #5 | RSM meeting complete | RSM meeting notes | Email draft to client: strategy + meeting outcomes (PSL) | AE / AAE |
| Submission | Auto ID Updates | #7 | Auto exposures finalized | Auto exposures | Email draft to Auto ID team; auto schedule extracted and stored year-over-year | AR |
| Proposal | T&D Generator | #9 | Quotes finalized | Quotes | Transparency & Disclosure document | AR |
| Bind | Bind Order | #1 | AE decision to bind | Quotes/submission portal data | Draft bind order email to client | AE / AAE |
| Post Binding | PTL Generator + PG Generator + SOI Generator | #9 | Policies reviewed | Policies | Policy Transmittal Letter + draft email to client; Program Graphic; Schedule of Insurance | AR |

**Notes:**
- PSL Builder is the stored source of truth for submission/proposal/binding requirements downstream — it's not just an email, it's a record. LLM API likely required for drafting from meeting notes; blocked until API key available
- Certificate List (originally here, moved to Renewal Pipeline) — it's pipeline-connected, not a standalone doc generator
- Auto ID Updates stored year-over-year with ops team access — requires a persistent data store, same pattern as Certificate List
- PTL Generator, PG Generator, and SOI Generator are grouped at Post Bind #9 — three outputs from the same policy data, build as one skill with selectable output types
- PTL mirrors BTL Generator (Bind #9 → CBI Generator + BTL Generator)

---

### System Updates

**Purpose:** Automates updates to external systems — the agency management system (AMS), the certificate system, and the document management system (DMS). Fires at milestone dates or on checkbox/mail activity. Replaces manual system navigation with a unified interface connected to config data.

**Active stages:** Renewal Prep, Proposal, Bind, Post Binding

| Stage | Skill | Task Ref | Trigger | Input | Output | Owner |
|---|---|---|---|---|---|---|
| Renewal Prep | Header Updates | #1 | 180 days pre-renewal | Select coverages renewing from last year | Renewed submission headers in the AMS | AR |
| Renewal Prep | Team Confirmer | #8 | Manual / on demand | Config (team members) | Emails to placement / client service colleagues confirming renewal assignment | AR |
| Proposal | Header Updates | #4 | Indications received | Indications | Recorded market responses in the AMS | AR |
| Bind | Status Updater + Certificate Updater | #4, #5 | Binders received; checkbox activity | Mail activity; binders | Client team status email; updated coverages in the certificate system | AR |
| Post Binding | Document Checker | #2 | Subjectivities list | Subjectivities | Carrier subjectivity closure status updated; saved to the DMS | AAE / AR |

**Notes:**
- Header Updates appears at two stages (Renewal Prep #1 and Proposal #4) — same skill, different trigger and input. One Blueprint, context-aware behavior
- Header Updates at Renewal Prep: one/two button interaction; auto-prompts placement member at 180 days
- Certificate Updater should follow the same UI/UX pattern as Loss Run — upload binders, extract coverage data, review table, confirm update
- Bind #2 (Advise Placement to Bind) dropped — legal/compliance concerns flagged; no skill assigned

---

### Meeting Scheduler

**Purpose:** Schedules internal and external meetings at key renewal touchpoints. Reads calendar availability from config and produces ready-to-send invites. Calendar integration required.

**Active stages:** Renewal Prep, Post Binding

| Stage | Skill | Task Ref | Trigger | Input | Output | Owner |
|---|---|---|---|---|---|---|
| Renewal Prep | Renewal Kickoff (email) | #5 | Renewal initiated | Config (team members) | Draft kickoff email to client team | AE / AAE / AR |
| Renewal Prep | Meeting Scheduler (ISM) | #6 | AE/AAE align on Internal Strategy Meeting (ISM) timing | Config (calendars) | Available timeslots visualized in UI; calendar invite sent to all team members needed | AAE |
| Post Binding | Meeting Scheduler (Post-Bind) | #1 | Post-bind milestone reached | Invoicing status; binders; post-bind subjectivities | Internal meeting scheduled; updated status | AE / AAE / AR |

**Notes:**
- Renewal Kickoff (#5) produces the draft email; Meeting Scheduler (#6) handles calendar availability and invite — two separate outputs from adjacent tasks, potentially one combined skill
- Timeslots should be visualized in the dashboard UI before sending — not a blind calendar invite
- Calendar integration reads from config (team members' calendars via Microsoft Graph, already connected)

---

### Invoicing Assistant

**Purpose:** Manages the invoicing and post-bind policy tracking workflow. Captures invoicing stage per coverage line (Not Started / Pending / Invoiced) and tracks outstanding policy receipt with escalation flags.

**Active stages:** Proposal, Invoice, Post Binding

| Stage | Skill | Task Ref | Trigger | Input | Output | Owner |
|---|---|---|---|---|---|---|
| Proposal | Premium Financing | #12 | AE decision to offer financing | Premium totals | Draft premium financing email / notification in inbox | AE / AAE |
| Invoice | Invoicing Assistant | #1, #2, #3 | Bind complete (within 5 days) | Binders; CBI; Installment Schedules; CN; RMB ID; PayTo codes | Premium totals; installment schedules broken out by line; invoicing stage per coverage line | AAE / AR |
| Post Binding | Policy Tracker | #5 + #6 | 30 days post-bind | Binders; config (placement team members) | Policy receipt status per line; notifications to placement for outstanding policies; follow-up cadence set with AE | AR |

**Notes:**
- Invoicing Assistant covers all three invoice tasks (#1, #2, #3) as a single skill — escalation flag fires if required documents are missing or corrections needed
- Policy Tracker combines Post Bind #5 and #6 — one skill tracking receipt and follow-up
- Premium Financing at Proposal #12 is a lightweight notification trigger, not a full invoicing flow

---

## Live Skill Specs

### Loss Run Request

**Portal:** Document Review
**Trigger:** Renewal Preparation task #9 and Submission task #2
**Location:** `dashboard/skills/loss_run/`

**Flow:**
```
1. User clicks launch button on task #9 or #2
2. GET /skills/loss-run → upload page
3. User drops binder files (PDF, DOCX, XLSX, CSV) — multi-file batch
4. POST /skills/loss-run/upload-batch
     → service.py: Step 0a filename hints, Step 0b declarations page scoring
     → Pass 1 label:value scan → Pass 2 dense splitter → Pass 3 pdfplumber spatial
     → resolve_coverage(), lookup_carrier(), resolve_client(), score_confidence()
5. Step 2: editable batch review table (one row per file)
   → User sets batch-level Client/Insured; reviews/corrects Policy # | Carrier | Eff. Date | Coverage
6. POST /skills/loss-run/draft-batch → rows grouped by primary_email
7. Step 3: grouped draft cards per recipient, each with copy button
```

**Routes (live):**
- `GET  /skills/loss-run`               → upload page
- `POST /skills/loss-run/upload-batch`  → N files → list[ExtractedRow] JSON
- `POST /skills/loss-run/draft-batch`   → N confirmed rows → grouped drafts JSON
- `POST /skills/loss-run/rescan`        → single file, full_scan=True, returns updated row

**Current chunk status:**

| Chunk | Description | Status |
|---|---|---|
| 1 | Proof-of-concept: Chubb-only, hardcoded regex | Complete — replaced |
| 2 | Full rewrite: form-agnostic 3-pass extraction, editable UI | Complete |
| 3 | Batch upload: multi-file drop zone, review table, grouped draft cards | Complete |
| 4 | Extraction refinement: validator + tighter coverage cleanup | Partially complete |
| 5 | One-client batch UI: remove confidence col, widen table, batch-level insured | Complete |
| 6 | Declarations-first pipeline + rescan UI | In progress — on branch `chunk6-extraction` |
| 7 | LLM extraction fallback (Pass 4) | Blocked — API key needed |
| 8 | Direct Outlook send | Deferred |

**Design guardrails:**
- `service.py` has zero Flask imports — never add them
- `__init__.py` is a thin adapter — no business logic
- "Could not extract" is a UI state, not a crash — always return a row with empty fields
- Coverage aliases live in `config/coverage_aliases.json` — never hardcode in service.py
- All edits to `service.py` must use a Python patch script executed via bash (`python _patch_service.py`) — `edit_file` tool fails on this file due to encoding issues

**Source-of-truth files:**
- `docs/Skills/coverages.md` — approved coverage vocabulary
- `docs/Skills/companymap.md` — canonical carrier names + aliases
- `docs/Skills/emailmap.md` — carrier → email mapping
- `config/coverage_aliases.json` — 50+ product-name-to-canonical-category mappings
- `config/clients.json` — client list with aliases
- `data/loss_run_carriers.json` — carrier/contact DB

**Dependencies:** `pypdf`, `pdfplumber`, `python-docx`, `pandas`

---

### Invoicing Assistant

**Portal:** Invoicing Assistant
**Trigger:** Invoice task #3 (launch icon in the stage table); covers tasks #1-#3
**Location:** `dashboard_v2/skills/invoicing-assistant/`

**Flow:**
```
1. GET /skills/invoicing-assistant → client picker (CN + RMB shown) + binder drop zone
2. POST /skills/invoicing-assistant/upload-batch
     → identity via the v1 loss-run engine (insured, carrier, policy no., coverage, eff. date)
     → billing pages scored and re-read with layout preserved
     → money.py: premium, commission (% and $), taxes, fees, surcharges
     → each binder reconciled against the total it prints
3. Editable review: one card per binder, one row per money line, live placement totals
4. POST /skills/invoicing-assistant/recap  → paste-ready invoice request summary
   POST /skills/invoicing-assistant/export → CSV, one row per money line
```

**Routes (live):**
- `GET  /skills/invoicing-assistant`              → client + upload page
- `POST /skills/invoicing-assistant/upload-batch` → N binders → rows + placement summary JSON
- `POST /skills/invoicing-assistant/client`       → CN + RMB for one client
- `POST /skills/invoicing-assistant/recap`        → re-totals edited rows, returns summary text
- `POST /skills/invoicing-assistant/export`       → CSV download

**Reconciliation:** every binder is checked against the total it prints. Charges the premium
already contains are excluded, an unnamed state charge that closes the gap is adopted, and a
"Total Amount Due" that is really net of commission is recognised as such. A binder that still
does not reconcile says by how much rather than balancing quietly.

**Accuracy:** `eval/run_eval.py` grades premium, commission rate, charges and total against
hand-checked truth in `eval/truth/*.json` — 60/60 on set1 (15 binders).

**Design guardrails:**
- `money.py` has no Flask and no PDF imports — text in, amounts out, so the eval can grade it
- extraction never invents a figure: a missing premium stays empty and is flagged
- CN and RMB are read from `config/clients.json`, never typed into the skill

**Source-of-truth files:**
- `config/clients.json` — client list, aliases, and the `cn` / `rmb` billing identifiers
- `dashboard/skills/loss_run/service.py` — the shared identity extraction engine

**Dependencies:** `pypdf`, `pdfplumber`

---


### RSM Deck Builder

**Portal:** Deck Builder
**Trigger:** RSM task #2
**Location:** `dashboard/skills/rsm/`

**Flow:**
```
1. GET /skills/rsm → upload form + market slide catalog grouped by category
2. User provides: last year's RSM deck (required), client name, new policy year,
   old policy year (optional), program graphic (optional), market slides (checkbox)
3. POST /skills/rsm/build
     → ZIP-level assembly: template chrome + carried content + program graphic + market slides
4. Slide log preview rendered (title, source, type per slide)
5. GET /skills/rsm/download?key=...&filename=... → streams .pptx
```

**Routes (live):**
- `GET  /skills/rsm`         → upload form with market catalog
- `POST /skills/rsm/build`   → builds deck, returns JSON slide log + download key
- `GET  /skills/rsm/download` → streams assembled .pptx

**ZIP-level assembly rationale:** python-pptx cross-presentation copy API produces duplicate URIs, missing customXml, and broken Part aliasing. ZIP-level is the only approach that produces a file PowerPoint opens without repair.

**Supporting data:**
- `data/rsm_market_db.json` — market slide catalog
- `data/rsm_market_slides/` — uploaded market condition .pptx files
- `uploads/` — RSM template .pptx files; newest used as template

**Dependencies:** `lxml`, `python-pptx` (inventory only)

---

## Data Flow (v1 pipeline unchanged)

```
run_daily.py (unchanged)
    ↓ writes
workbook/chief_of_staff_tracker.xlsx
data/latest_changes.json

dashboard v1 / data_loader.py (unchanged)
    ← reads xlsx, clients.json, completions.json
    → structured dicts to Flask routes (port 5000)

dashboard v2 (new port)
    ← dual navigation: by stage (row) or by portal (column)
    ← mounts all skill Blueprints
    ← mounts all portal Blueprints

dashboard/skills/<name>/service.py
    ← receives user input (uploads, form data)
    → produces output (email drafts, extracted data, assembled decks)
    → never writes to workbook, never calls run_daily.py
```

The pipeline never calls skills. Skills never write to the workbook. Data flow is one-way.

---

## What This Document Is Not

- A task tracker — see `docs/build_plan.md`
- A loss run implementation guide — see `docs/Skills/LRbuild.md` and `docs/Skills/LRHO.md`
- A plain-English system overview — see `docs/new_handoff.md`
- The matrix source of truth — see `docs/Skills/portal_matrix.xlsx`
