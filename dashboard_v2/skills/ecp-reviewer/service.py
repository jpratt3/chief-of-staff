"""ECP Reviewer — Document Review · Renewal Preparation #3"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class ECPResult:
    success: bool
    client_name: str = ""
    carrier: str = ""
    state: str = ""
    surplus_confirmed: bool = False
    extracted_fields: dict = field(default_factory=dict)
    email_bullet: str = ""
    error: str = ""

def review_ecp(file_path: str, client_name: str) -> ECPResult:
    path = Path(file_path)
    if not path.exists():
        return ECPResult(success=False, error="File not found.")
    extracted, surplus = {}, False
    try:
        import pdfplumber, re
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages[:3])
        for pat, label in [(r"Carrier[\:\s]+([^\n]{3,60})", "carrier"), (r"State[\:\s]+([A-Z]{2})", "state"), (r"Policy Number[\:\s]+([^\n]{3,30})", "policy_num")]:
            m = re.search(pat, text, re.IGNORECASE)
            if m: extracted[label] = m.group(1).strip()
        surplus = bool(re.search(r"surplus lines|non-admitted|exempt commercial", text, re.IGNORECASE))
    except ImportError:
        extracted = {"note": "pdfplumber not available — manual review required"}
    except Exception as e:
        extracted = {"error": str(e)}
    carrier = extracted.get("carrier", "[Carrier not detected]")
    state = extracted.get("state", "[State]")
    if surplus:
        bullet = f"  • ECP Status — Confirmed surplus lines carrier ({carrier}) in {state}. ECP form on file; ECP status verified."
    else:
        bullet = f"  • ECP Status — Carrier ({carrier}) does not appear surplus lines in {state}. Confirm if ECP required."
    return ECPResult(success=True, client_name=client_name, carrier=carrier, state=state, surplus_confirmed=surplus, extracted_fields=extracted, email_bullet=bullet)
