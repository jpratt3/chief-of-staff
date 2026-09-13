"""Home State Assigner — Document Review · Renewal Preparation #4"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

US_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}

@dataclass
class HomeStateResult:
    success: bool
    client_name: str = ""
    detected_state: str = ""
    email_draft: str = ""
    confidence: str = "low"
    error: str = ""

def assign_home_state(file_path: str, client_name: str, prior_state: str = "") -> HomeStateResult:
    path = Path(file_path)
    if not path.exists():
        return HomeStateResult(success=False, error="File not found.")
    detected = prior_state.upper() if prior_state.upper() in US_STATES else ""
    confidence = "high" if detected else "low"
    try:
        import pdfplumber, re
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages[:4])
        m = re.search(r"Home State[\:\s]+([A-Z]{2})", text, re.IGNORECASE)
        if m and m.group(1) in US_STATES:
            detected = m.group(1); confidence = "high"
        elif not detected:
            m2 = re.search(r"(?:surplus lines|SL licensee)[^\n]{0,40}\b([A-Z]{2})\b", text, re.IGNORECASE)
            if m2 and m2.group(1) in US_STATES:
                detected = m2.group(1); confidence = "medium"
    except Exception:
        pass
    state_display = detected or "[State not detected]"
    draft = f"To: [Placement Rep — {state_display}]\nSubject: {client_name} — Home State Assignment: {state_display}\n\nHi,\n\nConfirming SL licensee for {client_name}, Home State: {state_display} (confidence: {confidence}).\n\nPlease confirm or advise on any changes.\n\nThanks,\nJordan\n"
    return HomeStateResult(success=True, client_name=client_name, detected_state=detected, email_draft=draft, confidence=confidence)
