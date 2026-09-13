"""Build the renewal-ready loss summary workbook.

The deliverable: {Account}_LossRunSummary_{year}.xlsx, delivered to
a SharePoint folder AND attached to the Applied Epic account record.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
from reconcile import Reconciliation
from schema import LossRunDocument

HDR_FILL = PatternFill("solid", fgColor="1F3350")
HDR_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(bold=True, size=14)
SUB_FONT = Font(color="5B6675", size=9)
TOTAL_FONT = Font(bold=True, size=10)
TOTAL_FILL = PatternFill("solid", fgColor="EEF1F4")
MONEY = '"$"#,##0'
THIN = Border(bottom=Side(style="thin", color="D9DEE5"))


def _hdr(ws, row: int, headers: list[str], widths: list[int]) -> None:
    for i, (h, w) in enumerate(zip(headers, widths), start=1):
        c = ws.cell(row=row, column=i, value=h)
        c.fill, c.font = HDR_FILL, HDR_FONT
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 20


def build_workbook(account: str, rec: Reconciliation, docs: list[LossRunDocument],
                   out_path: Path, valuation: date,
                   period: tuple[date, date], carrier_count: int) -> Path:
    wb = Workbook()

    # ---- Sheet 1: Summary --------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Loss Run Summary"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (f"{account}  ·  {period[0]:%m/%Y} – {period[1]:%m/%Y}  ·  "
                f"{carrier_count} carrier policies")
    ws["A2"].font = SUB_FONT
    ws["A3"] = f"Valued as of {valuation:%m/%d/%Y}  ·  prepared by LossRunAgent"
    ws["A3"].font = SUB_FONT

    _hdr(ws, 5, ["CLAIM", "CARRIER", "DATE OF LOSS", "LINE", "STATUS", "PAID", "INCURRED"],
         [16, 16, 15, 8, 10, 14, 14])
    by_carrier = {d.artifact: d.carrier for d in docs}
    r = 6
    for c in rec.canonical:
        ws.cell(row=r, column=1, value=c.claim_number).border = THIN
        ws.cell(row=r, column=2, value=by_carrier.get(c.source_ref.artifact, "")).border = THIN
        d = ws.cell(row=r, column=3, value=c.date_of_loss)
        d.number_format, d.border = "mm/dd/yyyy", THIN
        ws.cell(row=r, column=4, value=c.line).border = THIN
        ws.cell(row=r, column=5, value=c.status.title()).border = THIN
        p = ws.cell(row=r, column=6, value=c.paid)
        p.number_format, p.border = MONEY, THIN
        i = ws.cell(row=r, column=7, value=c.total_incurred)
        i.number_format, i.border = MONEY, THIN
        r += 1

    t = rec.totals
    ws.cell(row=r, column=1, value=f"{t['claims']} claims · {carrier_count} carrier policies")
    for col in range(1, 8):
        cell = ws.cell(row=r, column=col)
        cell.font, cell.fill = TOTAL_FONT, TOTAL_FILL
    ws.cell(row=r, column=5, value="TOTAL").font = TOTAL_FONT
    tp = ws.cell(row=r, column=6, value=t["paid"])
    tp.number_format, tp.font, tp.fill = MONEY, TOTAL_FONT, TOTAL_FILL
    ti = ws.cell(row=r, column=7, value=t["incurred"])
    ti.number_format, ti.font, ti.fill = MONEY, TOTAL_FONT, TOTAL_FILL
    ws.freeze_panes = "A6"

    # ---- Sheet 2: By Year --------------------------------------------------
    ws2 = wb.create_sheet("By Year")
    ws2["A1"] = "Loss Experience by Policy Year"
    ws2["A1"].font = TITLE_FONT
    _hdr(ws2, 3, ["POLICY YEAR", "LINE", "CLAIMS", "OPEN", "PAID", "INCURRED"],
         [14, 10, 10, 10, 14, 14])
    buckets: dict[tuple[int, str], dict] = {}
    for c in rec.canonical:
        k = (c.date_of_loss.year, c.line)
        b = buckets.setdefault(k, {"n": 0, "open": 0, "paid": 0.0, "inc": 0.0})
        b["n"] += 1
        b["open"] += 1 if c.status == "open" else 0
        b["paid"] += c.paid
        b["inc"] += c.total_incurred
    r = 4
    for (yr, line), b in sorted(buckets.items()):
        ws2.cell(row=r, column=1, value=yr).border = THIN
        ws2.cell(row=r, column=2, value=line).border = THIN
        ws2.cell(row=r, column=3, value=b["n"]).border = THIN
        ws2.cell(row=r, column=4, value=b["open"]).border = THIN
        p = ws2.cell(row=r, column=5, value=round(b["paid"], 2))
        p.number_format, p.border = MONEY, THIN
        i = ws2.cell(row=r, column=6, value=round(b["inc"], 2))
        i.number_format, i.border = MONEY, THIN
        r += 1

    # ---- Sheet 3: Large Losses --------------------------------------------
    ws3 = wb.create_sheet("Large Losses")
    ws3["A1"] = f"Large Losses (incurred ≥ ${config.LARGE_LOSS_THRESHOLD:,})"
    ws3["A1"].font = TITLE_FONT
    _hdr(ws3, 3, ["CLAIM", "CARRIER", "DATE OF LOSS", "STATUS", "PAID", "INCURRED", "SOURCE"],
         [16, 16, 15, 10, 14, 14, 34])
    r = 4
    for c in sorted((c for c in rec.canonical
                     if c.total_incurred >= config.LARGE_LOSS_THRESHOLD),
                    key=lambda c: -c.total_incurred):
        ws3.cell(row=r, column=1, value=c.claim_number).border = THIN
        ws3.cell(row=r, column=2, value=by_carrier.get(c.source_ref.artifact, "")).border = THIN
        d = ws3.cell(row=r, column=3, value=c.date_of_loss)
        d.number_format, d.border = "mm/dd/yyyy", THIN
        ws3.cell(row=r, column=4, value=c.status.title()).border = THIN
        p = ws3.cell(row=r, column=5, value=c.paid)
        p.number_format, p.border = MONEY, THIN
        i = ws3.cell(row=r, column=6, value=c.total_incurred)
        i.number_format, i.border = MONEY, THIN
        ws3.cell(row=r, column=7, value=c.source_ref.cite()).border = THIN
        r += 1

    # ---- Sheet 4: Sources --------------------------------------------------
    ws4 = wb.create_sheet("Sources")
    ws4["A1"] = "Document Provenance"
    ws4["A1"].font = TITLE_FONT
    _hdr(ws4, 3, ["ARTIFACT", "CARRIER", "KIND", "CHANNEL", "VALUATION", "CLAIMS"],
         [34, 16, 20, 12, 14, 10])
    r = 4
    for d in sorted(docs, key=lambda d: (d.carrier, d.artifact)):
        ws4.cell(row=r, column=1, value=d.artifact).border = THIN
        ws4.cell(row=r, column=2, value=d.carrier).border = THIN
        ws4.cell(row=r, column=3, value=d.doc_kind).border = THIN
        ws4.cell(row=r, column=4, value=d.source).border = THIN
        v = ws4.cell(row=r, column=5, value=d.valuation_date)
        v.number_format, v.border = "mm/dd/yyyy", THIN
        ws4.cell(row=r, column=6, value=len(d.claims)).border = THIN
        r += 1

    r += 1
    ws4.cell(row=r, column=1, value="Reconciliation notes").font = TOTAL_FONT
    r += 1
    for n in rec.development:
        ws4.cell(row=r, column=1, value=n.note).font = SUB_FONT
        r += 1
    for d in rec.discrepancies:
        state = f"approved: {d.approved_value}" if d.resolved else "pending review"
        ws4.cell(row=r, column=1,
                 value=(f"[{d.id}] {d.headline} — {d.claim_number}: "
                        f"{d.pick_value} ({d.pick_ref.cite()}) over "
                        f"{d.other_value} ({d.other_ref.cite()}) — {state}")).font = SUB_FONT
        r += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
