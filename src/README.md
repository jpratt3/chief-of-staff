# `src/` — the inference pipeline

**Status: Orphaned.** Read this section before the rest.

The 20 modules here are complete, internally consistent and individually documented.
The orchestrator that called them — `run_daily.py` — is **not in this tree**. Only a
stale `__pycache__/run_daily.cpython-312.pyc` survives.

So this layer is documented as built, not as currently runnable. Everything below
describes what it does and did; none of it is a claim that you can clone this and run
it this morning. Restoring it means rewriting the ~200-line orchestration that wires
the stages together in the order given under *The run*.

---

## What it does

Turns a day of Outlook activity into structured, domain-aware work items, then emails
a briefing about what changed.

The interesting problem is not reading mail — it's deciding what an email *means*.
An attachment named `LR_2026.pdf` in a thread titled `RE: FW: renewal` is either a
loss run that closes a Prep task or an unrelated forward. The pipeline resolves that
through layered inference rather than a single rule.

## The run

1. **Read.** `outlook_client` opens a COM connection to the desktop profile.
   `mail_reader` / `mail_folder_reader` / `calendar_reader` pull the window (1 day
   daily, 60 on bootstrap) into `MailMessage` and `CalendarEvent` dataclasses.

2. **Classify.** `mail_rules` tags each message with the document types and stage
   signals it carries.

3. **Resolve.** `client_resolver` answers *which client is this?* in three layers,
   in order: the Outlook folder it arrived in; a subject-line match against the
   client gazetteer (longest alias first, so a short alias can't win over a longer
   one that contains it); then a team-member sender check. Anything unresolved is
   recorded rather than dropped.

4. **Infer.** `inference_engine` converts classified mail and events into
   `ResolvedActivity` objects — the domain model that exists *after* resolution, as
   distinct from the raw Outlook objects.

5. **Derive status.** `status_logic` maps activities + the renewal schedule onto one
   `WorkItemStatus` per canonical stage.

6. **Diff.** `state_store` snapshots before the run and compares after, producing
   `latest_changes.json` — the true deltas.

7. **Write and send.** `workbook_writes` updates the tracker; `briefing_builder`
   formats the briefing; `briefing_sender` sends it through Outlook.

## The two decisions that carry the design

**Status derivation lives in exactly one file.** Nothing outside
[`status_logic.py`](status_logic.py) decides what a work item's status is — not the
workbook layer, not the briefing, not a spreadsheet formula. The constraint is what
keeps the rules auditable, and it forced an uncomfortable question early: what does
"done" actually mean for each stage of a renewal? The status ladder is the answer:

```
Upcoming     due date > 14 days out, no activity
Not Started  due date <= 14 days out, no activity
In Progress  at least one qualifying signal
Complete     all required close signals present
Overdue      due date passed without reaching Complete
```

Close signals are per stage. Renewal Preparation needs any one of {ISM (Internal Strategy Meeting) scheduled,
exposure request sent, loss runs requested} to move, and all three to close.
Invoice is deliberately soft — an inbound premium invoice moves it to In Progress,
but this layer will never set it Complete from a signal alone.

**Delta-only alerting.** An earlier version re-surfaced all ~40 items every morning
regardless of change. Within a week it was being skimmed and then ignored — the
briefing had no information content. Snapshot-and-diff fixed it: if an item appears,
something actually happened. That is the bar that makes any automated digest worth
opening.

Section order follows the same logic — Changes, then Action Required, then Upcoming,
then In Progress by client, then Quiet Clients. An earlier build led with In Progress
and buried the two items that needed attention behind forty that didn't.

`Quiet Clients` is the section that justifies the whole thing: accounts with an
upcoming deadline and no activity in 14+ days. Clients with busy threads never get
forgotten; quiet ones with real deadlines do.

---

## Modules

| Module | LOC | Responsibility |
|---|---:|---|
| `outlook_client.py` | 99 | COM connection to the desktop Outlook profile |
| `mail_reader.py` | 170 | Inbox/Sent within the lookback window |
| `mail_folder_reader.py` | 132 | Per-client folder traversal |
| `calendar_reader.py` | 121 | Calendar within lookback + lookahead |
| `mail_rules.py` | 64 | Document-type and stage-signal tagging |
| `client_resolver.py` | 465 | Folder → subject → sender client resolution |
| `inference_engine.py` | 362 | Classified inputs → `ResolvedActivity` |
| `renewal_calendar.py` | 288 | Renewal schedule and canonical stage order |
| `status_logic.py` | 583 | **Sole** authority on work item status |
| `state_store.py` | 273 | Snapshot / compare / persist |
| `briefing_builder.py` | 379 | Formats the plain-text briefing |
| `briefing_sender.py` | 53 | Sends it via Outlook |
| `workbook_manager.py` | 174 | Opens and validates the tracker |
| `workbook_writes.py` | 930 | All sheet writes |
| `health_checks.py` | 33 | Preflight |
| `models.py`, `mail_models.py`, `calendar_models.py` | 76 | Dataclasses |
| `logging_utils.py` | 36 | Run logging |

`models.ResolvedActivity` intentionally does not merge with `MailMessage` or
`CalendarEvent`: those are raw Outlook shapes, this is the post-inference domain
model. Collapsing them would put resolution state on objects that don't have it yet.

## Why COM and not Microsoft Graph

The pipeline talks to the desktop Outlook client through `pywin32`, against the
profile already signed in. No app registration, no admin consent, no token cache, no
client secret — which is the entire reason it could exist. Graph access would have
required an approval cycle the project never needed.

A `graph_client` experiment predates this; only a compiled artifact remains, and
nothing imports it.

## Configuration

`config/settings.json` and `config/clients.json` are gitignored — they hold the real
client roster, renewal dates and colleague contacts. Copy the `.example` files.

Firm-specific identifiers (the premium-invoice mailbox that moves an Invoice item to
In Progress, the firm's legal name used in generated request signatures) read from
`settings.json → identity`, falling back to placeholders. No internal mailbox appears
in source.
