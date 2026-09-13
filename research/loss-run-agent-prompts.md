# Prompt series — build LossRunAgent (demo)

Target: a working, demoable prototype of "loss runs on autopilot" — renewal calendar → portal/email retrieval → extraction → standardized summary → human review → delivery.

How to use: run these sequentially in one repo, one prompt per Claude Code session. Each prompt opens with enough context to survive a fresh session.

---

## UI SPEC — the surfaces to build

Design spec written before any code. Everything below fixes the naming, states, and layout the implementation has to hit, so the prompts downstream have something concrete to be graded against.

### Hero — the deliverable
**"Loss Run Summary"** card, badge `Renewal ready`
Subhead: `Meridian Logistics · 01/2021 – 12/2025 · 12 carriers`
Table: CLAIM | CARRIER | DATE OF LOSS | PAID | INCURRED
```
WC-4471902   Northbridge    03/14/2022   $18,240    $18,240
GL-0088314   Harborstone    11/02/2022   $132,500   $164,000
AU-7712045   Summit         06/29/2023   $41,780    $47,300
WC-4471988   Northbridge    02/08/2024   $6,915     $9,400
PR-2210567   Ironwood       09/17/2024   $0         $25,000
```
Footer row: `47 claims · 12 carriers` … TOTAL `$412,655` `$538,190`
Caption chip: `✦ Completed by LossRunAgent in 4m 12s`
Background: two portal windows — `portal.northbridge.com/lossruns` ("Loss Run Request", date fields) and `portal.harborstone.com/claims-history` ("Claims History", `Downloading`, files `HBS_LossRun_2021-2025.pdf` / `HBS_LargeLoss_Detail.pdf` with progress bar).

### Panel 1 — "Every carrier, tracked in one place"
Card header `Gathering loss runs` / `Meridian Logistics · renewal 03/01` / badge `8 of 12`
| Carrier | Channel | Status |
|---|---|---|
| Northbridge | `Portal` | ✅ Downloaded |
| Harborstone | `Portal` | ⟳ Downloading |
| Summit | `Email` | ◷ Request sent · 2d ago |
| Ironwood | `Email` | ✉ Follow-up sent |
| Bluehaven | `Portal` | ⚠️ **Input required** (amber — needs a human) |
| Redstone | `Portal` | ✅ Downloaded |

### Panel 2 — "Portals navigated for you"
Status line above the window: `✦ Signed in, set 5-year range, generating report`
Window `portal.northbridge.com/lossruns` → **Loss Run Report**, right-aligned `POLICY WC-4471902`
Fields: `FROM 01/01/2021` `TO 12/31/2025` `FORMAT PDF · Detail`
`[Generate Report]` … `◌ Report generated in 41s`
Result row: `NBM_LossRun_2021-2025.pdf` · `1.4 MB · 9 pages` · ✅ Downloaded

### Panel 3 — "Delivered where you work"
`APPLIED EPIC · ACCOUNT` → **Meridian Logistics** → ATTACHMENTS
- `Meridian_LossRunSummary_2026.xlsx` — *Added by LossRunAgent · just now*
- `Meridian_Application_Signed.pdf` — *Added by J. Reyes · 12 Feb*

Overlay card: `SharePoint / Renewals / 2026 / Meridian` → `Meridian_LossRunSummary_2026.xlsx` · `47 claims · 12 carriers · 218 KB` ✅

> **Note the output format is .xlsx, not PDF.** And delivery is dual: SharePoint folder *and* an Applied Epic account attachment.

### Panel 4 — "Starts from the renewal calendar"
`APPLIED EPIC · EXPIRATION REPORT` → **Renewals in 90 days** → badge `✦ Watching`
| ACCOUNT | EXPIRES | LOSS RUNS |
|---|---|---|
| Meridian Logistics | 03/01/2026 | ⟳ Gathering · 8 of 12 |
| Kestrel Foods Group | 03/15/2026 | ✅ Summary filed |
| Aldridge Manufacturing | 04/01/2026 | ⟳ Requests sent · 6 |
| Northvale Transit | 04/12/2026 | ◷ Queued for 01/12 |

("Queued for 01/12" against a 04/12 expiration ⇒ work kicks off ~90 days out.)

### Panel 5 — "Chases carriers without a portal"
Status line: `✦ No portal for this carrier, so the agent emails`
```
To       lossruns@ironwood.com
Subject  Loss run request · Meridian Logistics · PR-2210567

Hello,

Requesting 5-year currently valued loss runs for Meridian Logistics, policy
PR-2210567, 01/01/2021 through 12/31/2025. Renewal is 03/01.

Thank you,
Meridian Risk Services
```
Follow-up card: `FOLLOW-UP 2 · SENT AUTOMATICALLY` … right-aligned `Day 6`
> "Following up on the loss run request below for Meridian Logistics. Renewal is 03/01, so we would appreciate anything you can send this week."

### Panel 6 — "Live in minutes, not quarters" (Tempo, the agent builder)
Chat UI, header `✦ Tempo`, badge `Draft`
- **You:** "Build an agent that pulls loss runs from carrier portals and email, then produces a renewal-ready summary in Applied Epic."
- **✦ LossRunAgent:** "Done. Here's your agent."
- Result card: **Loss Runs Assistant** · `4 tools · 2 procedures`
  - Channels: `Portal` `Email`
  - Procedure: `Loss run intake AOP` `Normalize`
  - Tools: `Carrier portals` `Applied Epic` `SharePoint`

> **AOP = Agent Operating Procedure** — the prose-SOP model, named.

### Panel 7 — "Your team stays in the loop" (review queue)
Amber header: `⚠️ Amounts disagree across carriers` … right-aligned `1 of 3`
`Claim GL-0088314` · `Harborstone · 11/02/2022`
| Source | File | Value | |
|---|---|---|---|
| Carrier loss run | `HBS_LossRun_2021-2025.pdf · p.4` | **$164,000** | `Agent's pick` |
| Large loss detail | `HBS_LargeLoss_Detail.pdf · p.1` | $158,500 | `Superseded` |

`[Approve $164,000]` `[Open source page]` … *Logged to audit trail*

> Key nuance: the agent **commits to a value** and labels the loser "Superseded." The human confirms rather than adjudicates from scratch. Copy this — it's much better UX than a neutral side-by-side.

### Target numbers
Agent run time `4m 12s` end to end for a 12-policy book; extraction accurate enough that the review queue carries only genuine conflicts.

### Naming
Carriers: Northbridge, Harborstone, Summit, Ironwood, Bluehaven, Redstone.
Insureds: Meridian Logistics, Kestrel Foods Group, Aldridge Manufacturing, Northvale Transit.
Broker: Meridian Risk Services. Files: `NBM_LossRun_2021-2025.pdf`, `HBS_LargeLoss_Detail.pdf`, `Meridian_LossRunSummary_2026.xlsx`.

---

## Core design decisions (change in Prompt 0 if you disagree)

- **Demo world, not production.** Mock carrier portals + seeded PDFs so it demos without real credentials. Ports to real portals by swapping targets, not rewriting.
- **Playwright, not screenshot computer-use.** Deterministic and free. The SOP layer is interface-isolated so a computer-use executor can drop in later (stretch 7a) — that's the flakiest part, so it stays behind an interface.
- **The prose AOP is the product.** Numbered natural-language steps with typed chips (`data.X.Y`, `auth.System.field`), self-correction instructions, CRITICAL guardrails. Not a node graph.
- **Python/FastAPI backend**, Claude API tool-use for the agent, minimal web UI.

---

## Prompt 0 — Architecture pass (optional, plan mode)

> I'm building a demo of an agentic loss-run retrieval system for insurance brokers. Workflow: an Applied Epic expiration report lists renewals in the next 90 days → for each policy, an agent either logs into the carrier portal (set 5-year valuation range, generate report, download PDF) or emails the carrier and follows up on a cadence until the report arrives → PDFs in heterogeneous layouts are extracted into a canonical claims schema → the system produces a standardized loss summary XLSX → conflicts between documents are flagged with a proposed resolution and page-level citations for human approval → the summary is delivered to a SharePoint folder and attached to the Applied Epic account record.
>
> Constraints: Python/FastAPI, Playwright for browser automation, Claude API (tool use) as the agent brain, all against mock services I'll build locally. Agent behavior must be driven by a natural-language Agent Operating Procedure with typed variable chips (`data.Renewal.policy_number`, `auth.Northbridge.password`) — no hardcoded per-carrier scripts in the orchestrator.
>
> Don't write code. Give me: repo structure, the component boundary between "AOP executor" and "tools" (so Playwright can later be swapped for a computer-use executor), the canonical claims schema, and where run-trace capture hooks in. Flag the two riskiest integration points.

---

## Prompt 1 — The demo world (mock Applied Epic, carrier portals, inbox)

> Build the demo environment for an agentic loss-run system. Empty repo; Python 3.12, FastAPI.
>
> **1. Mock Applied Epic** (`mock_epic/`, port 8100):
> - `GET /expirations?days=90` → expiration report: account name, policy number, line, carrier, effective/expiration dates, servicing team. Seed the four accounts from the spec: Meridian Logistics (03/01/2026), Kestrel Foods Group (03/15/2026), Aldridge Manufacturing (04/01/2026), Northvale Transit (04/12/2026) — with Meridian carrying 6 carriers across WC/GL/Auto/Property.
> - `GET /accounts/{id}`, `POST /accounts/{id}/attachments` (name, added_by, timestamp), `POST /accounts/{id}/activities`.
>
> **2. Four mock carrier portals**, server-rendered HTML, ports 8201–8204, each deliberately different:
> - **Northbridge** (8201): login → TOTP 2FA → policy search → "Loss Run Report" form with FROM/TO date fields and a FORMAT select (`PDF · Detail` / `PDF · Summary`) → async "generating…" page ~5s → download. Mirror the spec's layout including the `POLICY WC-4471902` header and a "Report generated in 41s" line.
> - **Harborstone** (8202): login → "Claims History" page listing *two* downloadable documents (`HBS_LossRun_2021-2025.pdf` and `HBS_LargeLoss_Detail.pdf`) — the agent must take both; they are the source of the seeded discrepancy.
> - **Bluehaven** (8203): login → **requires a security question the vault has no answer for** → this is the `Input required` case that must escalate to a human, not fail silently.
> - **Redstone** (8204): login → policy search that REJECTS policy numbers containing dashes, with an on-screen message stating the required format ("enter as RS0000000, no separators") → date range → immediate download. This is the self-correction test.
> - **Summit and Ironwood have no portal** (email-only, Prompt 3).
>
> **3. Seeded loss run PDFs** — reportlab, one distinct layout per carrier (different column names — "Incurred" vs "Total Inc." vs split paid/reserved — different date formats and orderings). 5 policy years, mix of WC/GL/Auto/Property. Portals stamp the requested valuation date on them. Use the spec's claim numbers/amounts so the final summary matches the hero card (47 claims, 12 carriers, $412,655 paid / $538,190 incurred).
>   - **Seed a legitimate development pattern**: one claim at an older valuation shows incurred $48,200; at a newer valuation $131,700 (reserve strengthening). NORMAL — must not be flagged.
>   - **Seed three true discrepancies** (the spec shows "1 of 3"), one of which is exactly the spec case: claim `GL-0088314` (Harborstone, DOL 11/02/2022) shows **$164,000** on `HBS_LossRun_2021-2025.pdf` p.4 and **$158,500** on `HBS_LargeLoss_Detail.pdf` p.1. The other two: a conflicting date of loss, and a claim present in one document and absent from a same-period document.
>
> **4. Mock inbox** — maildir-style `mock_mail/{inbox,sent}` plus `scripts/carrier_reply.py` simulating Summit and Ironwood replying with PDF attachments after N follow-ups (Summit after 1, Ironwood after 2).
>
> **5. Credential vault stub** — `vault.json` (gitignored) mapping `auth.<Carrier>.username/password/totp_secret`; ship `vault.example.json`. TOTP via pyotp so Northbridge's 2FA actually verifies. Bluehaven deliberately has no `security_answer` key.
>
> Acceptance: one command starts Epic + all 4 portals; I can manually log into each and download a PDF; Bluehaven blocks at the security question; Redstone rejects `RS-221-0567` and accepts `RS2210567`; the three seeded discrepancies exist; README documents every port and login.

---

## Prompt 2 — AOP format + agent executor

> Repo context: mock Applied Epic (8100), 4 mock carrier portals (8201–8204), `vault.json`, seeded loss run PDFs — all built. Now the core: a prose-AOP-driven agent executor.
>
> **1. AOP format** (`aops/*.md`) — numbered natural-language steps in the register of an internal training doc. Typed chips inline: `{data.Renewal.policy_number}`, `{data.Renewal.carrier}`, `{auth.Northbridge.totp_secret}`. Steps may carry tool-scoped blocks (`[browser]`, `[email]`, `[files]`) and guardrail lines (`CRITICAL: ...`). Write `aops/loss_run_intake.md` covering: pull renewal from Epic → branch portal vs email by carrier → per-carrier sub-procedures → download and store artifacts → hand to extraction.
>   - Redstone's sub-procedure must include, in this style: *"If the search is rejected because of the number format, read the on-screen guidance, reformat the policy number to match, and search again until the policy is found."*
>   - Harborstone's must instruct taking **both** documents.
>   - A global guardrail: *"CRITICAL: if the portal asks for information not in the vault, do not guess — escalate with `Input required` and stop."*
>
> **2. Executor** (`agent/executor.py`) — Claude API tool use (claude-opus-5, low temperature). The AOP with `data.*` chips resolved becomes the system prompt; `auth.*` chips are passed **by name only** — the tool layer injects real secrets so they never enter model context. Tools:
> - `browser_*`: goto, read_page (DOM text + form fields), click, fill, select, download — Playwright, one context per run. `browser_fill` takes `secret_ref="auth.X.password"` and resolves inside the tool.
> - `save_artifact(name, source)` → `runs/{run_id}/artifacts/`
> - `epic_get_renewal`, `epic_post_attachment`, `epic_post_activity`
> - `done(status, summary)` / `escalate(reason)`
>
> **3. Run trace** (`runs/{run_id}/trace.jsonl`) — every tool call: timestamp, AOP step, tool, args (secrets redacted), result snippet, plus a full-page screenshot per browser action into `artifacts/screens/`. Feeds the Prompt 6 UI.
>
> **4. CLI**: `python -m agent run --policy <n> --aop aops/loss_run_intake.md`
>
> Acceptance: Northbridge completes login + TOTP + date range + format select + download unassisted; Redstone demonstrably hits the format rejection and self-corrects (visible in trace); Harborstone yields two artifacts; Bluehaven escalates as `Input required` without looping or guessing; `grep` proves no secret appears in trace.jsonl.

---

## Prompt 3 — Email chase (Summit, Ironwood)

> Repo context: AOP executor works against 4 mock portals; maildir at `mock_mail/`; Summit and Ironwood have no portal. Build the email path.
>
> **1. Email tools**: `email_send(to, subject, body, attachments)` → `mock_mail/sent`; `email_check(thread_id)` → scans inbox for replies with attachments.
> **2. Chase state machine** (`agent/chase.py`): SENT → FOLLOWUP_1 (day 3) → FOLLOWUP_2 (day 6) → ESCALATED (day 14 → review queue). Demo clock: `--tick` advances a virtual day. Match the spec cadence — the follow-up card is labelled `FOLLOW-UP 2 · SENT AUTOMATICALLY · Day 6`.
> **3. AOP extension** — add the email branch to `aops/loss_run_intake.md`, with the request format from the spec:
> ```
> Subject: Loss run request · {data.Renewal.account} · {data.Renewal.policy_number}
>
> Hello,
>
> Requesting 5-year currently valued loss runs for {data.Renewal.account}, policy
> {data.Renewal.policy_number}, {data.Renewal.period_start} through {data.Renewal.period_end}.
> Renewal is {data.Renewal.expires_short}.
>
> Thank you,
> {data.Broker.name}
> ```
> Follow-ups are model-generated from tone guidance in the AOP, not templates, but must reference the original request, the policy number, and the renewal date — the spec follow-up reads: *"Following up on the loss run request below for Meridian Logistics. Renewal is 03/01, so we would appreciate anything you can send this week."*
> On reply: validate the attachment is actually a loss run for the right policy before accepting it.
> **4. Wire in `scripts/carrier_reply.py`** so the demo shows: request → silence → follow-up → PDF arrives (Summit) and request → silence → follow-up → silence → follow-up → PDF (Ironwood).
>
> Acceptance: full chase runs via `--tick` in one command; a wrong-policy attachment (add this case) is rejected and the chase continues; day-14 escalation surfaces in run state; tracker statuses match the spec vocabulary (`Request sent · 2d ago`, `Follow-up sent`).

---

## Prompt 4 — Extraction, canonical schema, XLSX summary

> Repo context: agent retrieves loss run PDFs from 4 portals + email into `runs/{id}/artifacts/`. Six carrier layouts exist. Build extraction and the deliverable.
>
> **1. Canonical schema** (`schema.py`, pydantic): `LossRunDocument{carrier, policy_number, policy_period, valuation_date, source(portal|email), claims[]}`; `Claim{claim_number, date_of_loss, line, status, description, paid, expense, reserved, total_incurred, litigation_flag}`. Every field carries `source_ref` = (artifact, page, line/region). **Citations are load-bearing** — the review UI links to the exact page.
> **2. Extractor** (`extract/`) — Claude with the PDF via the API's document support, returning strict JSON against the schema. No per-carrier parsing code; the model absorbs layout variance. Validate with pydantic; on failure, one self-repair round-trip with the error message; then fail loudly.
> **3. Standardized summary → XLSX** (openpyxl). Match the spec deliverable `{Account}_LossRunSummary_{year}.xlsx`:
> - Sheet 1 "Summary": header block (account, period `01/2021 – 12/2025`, carrier count, valuation date), then the claim table CLAIM | CARRIER | DATE OF LOSS | PAID | INCURRED, totals row `47 claims · 12 carriers` with paid/incurred sums.
> - Sheet 2 "By Year": policy year × line — claim count, open count, paid, incurred.
> - Sheet 3 "Large Losses": claims over a configurable threshold (default $25k).
> - Sheet 4 "Sources": every artifact, carrier, channel, retrieval timestamp, page count.
> **4. Reconciliation** (`extract/reconcile.py`) — across documents covering the same policy/claim:
> - Same claim, different valuation dates, incurred moved → **normal development**, annotate, do not flag.
> - Same claim, comparable valuation, conflicting fields → **discrepancy**.
> - Claim in one document, absent from a same-period document → **discrepancy**.
> Each discrepancy must carry a **proposed resolution**: pick the more authoritative source (full carrier loss run > supplemental detail document; newer valuation > older) and label it `Agent's pick`, the other `Superseded`, with a one-line rationale.
>
> Acceptance: all 6 layouts extract to valid schema; XLSX opens clean in Excel and its totals match the hero card; reconciliation on seeded data = exactly 3 flags, 0 false positives (the reserve-development case must NOT flag); the `GL-0088314` flag proposes $164,000 with `HBS_LossRun_2021-2025.pdf · p.4` cited.

---

## Prompt 5 — Orchestrator + dual delivery

> Repo context: executor (portal + email), extraction, reconciliation all work standalone. Tie together.
>
> **1. Orchestrator** (`orchestrator.py`) — reads the Epic expiration report, one job per renewal, fans out: portal carriers concurrently (asyncio, per-carrier Playwright contexts), email carriers into the chase machine. Job states: QUEUED → RETRIEVING → EXTRACTING → RECONCILING → NEEDS_REVIEW | READY → DELIVERED | FAILED. Per-carrier sub-state drives the `8 of 12` progress counter.
> **2. Scheduling** — jobs activate ~90 days before expiration; before that they sit `Queued for {date}` (matches the spec's Northvale Transit row).
> **3. Dual delivery** on READY/approved: XLSX written to `mock_sharepoint/Renewals/{year}/{Account}/` **and** posted as an Applied Epic account attachment with `added_by="LossRunAgent"`; activity note posted; job → DELIVERED.
> **4. Status API**: `GET /jobs`, `GET /jobs/{id}` (incl. trace), `GET /jobs/{id}/discrepancies`, `POST /discrepancies/{id}/approve`.
> **5. One-command demo**: `make demo` boots the world, runs the book, auto-advances the email clock, and ends with Meridian in NEEDS_REVIEW (3 discrepancies) + Bluehaven `Input required`, Kestrel DELIVERED, Aldridge mid-chase, Northvale queued — i.e. the spec expiration report reproduced live.
>
> Acceptance: `make demo` runs clean twice in a row (idempotent runs dir); portal jobs genuinely overlap in trace timestamps; a portal being down marks that one job FAILED with a readable reason without sinking the batch.

---

## Prompt 6 — Review UI + run trace viewer

> Repo context: orchestrator with status API on 8000; runs contain trace.jsonl + step screenshots; discrepancies carry proposed resolutions and page citations. Build the human-facing layer — this gets demoed, so it should look like a product. FastAPI + HTMX or a small React app, your call. Visual target: quiet, typographic, near-monochrome with a single amber accent for "needs you" — match the spec above.
>
> **1. Renewal board** — the Epic expiration report view: ACCOUNT | EXPIRES | LOSS RUNS with live status chips (`Gathering · 8 of 12`, `Summary filed`, `Requests sent · 6`, `Queued for 01/12`) and a `✦ Watching` badge. Click through to per-account carrier tracker: carrier | channel chip (`Portal`/`Email`) | status (`Downloaded`, `Downloading`, `Request sent · 2d ago`, `Follow-up sent`, `Input required` in amber).
> **2. Run trace** — left: AOP step timeline with status and timestamps, including sub-steps ("TOTP challenge answered", "Search rejected → reformatted policy number → retried"); center: screenshot strip synced to the selected step; right: metadata + artifacts. Every run is a reviewable artifact.
> **3. Review queue** — reproduce the spec card: amber header `Amounts disagree across carriers` with an `n of N` counter; claim number, carrier, date of loss; the two sources stacked with file · page reference and value, one badged `Agent's pick`, the other `Superseded`; `[Approve $164,000]` and `[Open source page]` (renders the cited PDF page inline, highlight the row if cheap); `Logged to audit trail` note. Approval releases the job to delivery and writes the decision into the trace.
> **4. AOP viewer** — renders `aops/loss_run_intake.md` with typed chips as colored pills (`data.*` green, `auth.*` blue) and CRITICAL lines bold. Read-only; it exists for the demo line *"the agent runs this document."*
>
> Acceptance: full flow clickable — board fills as `make demo` runs, Meridian lands in review, approving all 3 discrepancies flips it to DELIVERED and the XLSX appears in `mock_sharepoint/` and on the Epic account; trace view replays the Redstone self-correction with screenshots.

---

## Stretch prompts (only if the demo lands)

- **7a — Computer-use executor**: implement the same tool interface with Claude computer use driving a real browser via screenshots instead of Playwright selectors; run the identical AOP against Northbridge. Proves the AOP layer's portability claim, and is what real back-office systems (Citrix, green-screen terminals) would require.
- **7b — Tempo clone**: the builder from Panel 6. Free-text description → generated AOP draft + agent card (`Channels`, `Procedure`, `Tools`, `n tools · n procedures`) → human edits → publish. Demo line: "describe the workflow, get the agent."
- **7c — Simulations**: auto-generate N perturbed portal behaviors (slow loads, moved elements, new error strings, changed format rules) and score AOP completion rate. An eval-before-go-live loop, applied to browser automation.

## Demo script (2 minutes)

1. Open the AOP viewer: *"this English document is the entire program."*
2. `make demo` — renewal board fills live; portal jobs run in parallel while the email chase ticks.
3. Open the Redstone trace: rejection → read the on-screen rule → reformat → success. **The RPA-killer moment.**
4. Bluehaven stops at `Input required` — it didn't guess. Trust story.
5. Review queue: `GL-0088314`, $164,000 vs $158,500, both cited to a page. Note the reserve-development case that was correctly *not* flagged. Approve.
6. XLSX in `mock_sharepoint/Renewals/2026/Meridian/` and attached to the Epic account, "Added by LossRunAgent · just now."
