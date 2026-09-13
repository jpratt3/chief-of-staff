# `engine/` — document extraction and assembly

**Status: Live.** This is where the work is. No Flask imports anywhere in this
package: a dashboard, a CLI, a scheduled job or a test all call it the same way,
and the accuracy harness grades it without a request context.

```
engine/
  loss_run.py   carrier binder -> insured, policy, carrier, effective date, coverage
  rsm.py        cross-presentation slide assembly at the ZIP level
  base.py       SkillResult — the uniform return shape
  eval/
    run_eval.py         the accuracy harness
    truth/example.json  ground-truth schema (real sets gitignored)
```

---

## The problem `loss_run.py` solves

A broker requesting loss runs needs five facts off every binder: insured, policy
number, carrier, effective date, coverage. Those five sit in a different place on
every carrier's paperwork. There is no schema, no standard, and no cooperation
between carriers.

Template-per-carrier dies immediately — hundreds of carriers, each changing
layouts, and a package policy can name four policy numbers of which only one is
right. So nothing here assumes a format.

**Three layers:**

**1. Text.** `pdfplumber` in layout mode, which preserves column alignment. That
one choice is what makes `Label:   value`, header-row-over-value tables, and
two-column forms all reachable by the same rules. `pypdf` does a cheap first pass
for ranking. A shifted-encoding decoder repairs PDFs whose font maps come out
garbled.

**2. Page selection.** Pages are scored for declarations-likeness; only the best
few are re-read in the expensive layout mode. The first two pages always keep a
slot — cover letters name the insured and carrier without using any label.

**3. Fields as scored candidates.** Each field generates candidates from every
place its value could sit: same line, the column below, the line above, the
filename, free text. Best score wins. Labels that introduce *someone else's*
policy (`Followed Policy`, `Underlying`, `Quota Share`) push candidates down
rather than being ignored — on an excess binder those sections are real text that
looks exactly like the answer.

Entities come from gazetteers: carriers from `docs/Skills/companymap.md`, clients
from `config/clients.json`. A name the business already knows is recognised
anywhere on the page.

`"Could not extract"` is a UI state, not an error. Callers always get partial
results to correct by hand. A scanned PDF with no text layer still returns
whatever the filename yields, plus a note saying so.

---

## The eval

```bash
.venv/Scripts/python.exe engine/eval/run_eval.py
.venv/Scripts/python.exe engine/eval/run_eval.py set3      # one set
```

```
set1     160/160   100.0%
set2     150/150   100.0%
set3      88/90     97.8%
──────────────────────────
OVERALL  398/400  = 99.5%     (80 binders, 5 fields each)
```

Ground truth accepts a **list** of answers per field, not a string. This is the
decision that makes the harness honest: a package policy legitimately names four
policy numbers, a blended form is legitimately D&O *or* EPL *or* Fiduciary, and an
excess layer is legitimately the fronting carrier *or* the syndicate. Forcing one
answer would grade the engine against arbitrary choices and tune it toward noise.

Both current misses are one binder — a D&O excess layer, carrier resolved to the
fronting company instead of the syndicate, effective date empty. A fair miss, and
naming it is more useful than hiding it.

`truth/set*.json` and the binders they grade are gitignored (client filenames and
insured names). [`truth/example.json`](eval/truth/example.json) carries the schema
with fictional binders; the harness skips it unless you ask for it by name, and
ignores `_`-prefixed keys so a real set can carry its own notes.

**The rule that keeps it useful:** when a binder format breaks, add it to a truth
set *before* fixing the bug. The fix then stays fixed.

## Debugging a miss

[`debug_extraction.py`](../debug_extraction.py) prints, for any binder: which
pages were selected and how they scored, every candidate each field generated with
its score and provenance, and the winning value.

```bash
.venv/Scripts/python.exe debug_extraction.py "binders/some binder.pdf"
.venv/Scripts/python.exe debug_extraction.py --text "binders/some binder.pdf"
```

---

## `rsm.py` — ZIP-level slide assembly

Copies slides from market-update decks into a target presentation.

`python-pptx` has no correct cross-presentation slide copy, and every route
through its Part object model fails a different way: clearing `_sldIdLst` without
`drop_rel` duplicates part URIs; `relate_to()` drags in the source's transitive
relationships including unrelated slides; and `customXml` lives at the package
root, outside `ppt/`, where `Part.relate_to` cannot reach it.

The working approach operates at the ZIP byte level — rename referenced parts to
avoid collisions, copy every directly-referenced part (images, charts, tags,
notes, customXml, embeddings), rewrite `rId` targets, patch
`[Content_Types].xml` and `presentation.xml`. The result opens in PowerPoint
without a repair prompt.

Full reasoning is in that file's docstring.

---

## Consumers

Both v2 skills are thin adapters that reshape engine output for templates:

| Caller | Uses |
|---|---|
| `dashboard_v2/skills/loss-run/service.py` | `process_batch`, `build_draft_batch` |
| `dashboard_v2/skills/rsm/service.py` | `build_rsm`, `load_market_db` |
| `dashboard_v2/skills/invoicing-assistant/service.py` | `_pdf_page_texts`, `_pdf_layout_text`, `process_batch` — identity fields, so it never grows a second extractor |

Adapters must stay thin. **A rule added in an adapter is a rule the eval does not
measure.**

## Configuration

Firm-specific identifiers (the premium-invoice mailbox, the firm's legal name used
in generated request signatures) read from `config/settings.json → identity`,
falling back to placeholders. No internal mailbox appears in source.
