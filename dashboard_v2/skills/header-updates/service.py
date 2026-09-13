"""Header Updates — System Updates · Renewal Preparation #1"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

@dataclass
class HeaderResult:
    success: bool
    client_name: str = ""
    lines: List[str] = field(default_factory=list)
    ams_steps: List[str] = field(default_factory=list)
    confirmation_note: str = ""
    error: str = ""

def build_header_updates(client_name: str, lines: List[str], renewal_year: str) -> HeaderResult:
    if not client_name or not lines:
        return HeaderResult(success=False, error="Client and at least one line required.")
    steps = []
    for line in lines:
        steps.append(f'AMS: Renew "{line}" submission header → set year to {renewal_year}')
        steps.append(f'AMS: Confirm team assignments for "{line}"')
        steps.append(f'AMS: Update renewal date for "{line}"')
    note = f"HEADER UPDATE LOG — {client_name} ({renewal_year})\n\n" + "".join(f"  ☐ {s}\n" for s in steps) + "\nCompleted by: _______________  Date: ___________\n"
    return HeaderResult(success=True, client_name=client_name, lines=lines, ams_steps=steps, confirmation_note=note)
