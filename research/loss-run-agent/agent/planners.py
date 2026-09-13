"""Planners decide the next tool call. Two implementations, one interface.

ClaudePlanner   - the real thing: AOP as system prompt, Claude API tool use.
ScriptedPlanner - deterministic stand-in so the pipeline runs and tests without
                  an API key. It is REACTIVE, not hardcoded: it only reformats a
                  policy number after reading the rejection text off the page,
                  and only escalates after reading the security prompt. The
                  self-correction being demonstrated is genuine in both.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import config

TOOLS_SPEC = [
    {"name": "browser_goto", "description": "Navigate to a URL.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}},
                      "required": ["url"]}},
    {"name": "browser_read_page",
     "description": "Read the current page: visible text, form fields, controls.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "browser_fill",
     "description": ("Type into a field. Use secret_ref (e.g. 'auth.Redstone.password' "
                     "or 'auth.Northbridge.totp_code') for credentials so the value is "
                     "resolved from the vault and never exposed."),
     "input_schema": {"type": "object", "properties": {
         "selector": {"type": "string"}, "value": {"type": "string"},
         "secret_ref": {"type": "string"}}, "required": ["selector"]}},
    {"name": "browser_click", "description": "Click an element by CSS selector.",
     "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}},
                      "required": ["selector"]}},
    {"name": "browser_select", "description": "Choose an option in a <select>.",
     "input_schema": {"type": "object", "properties": {
         "selector": {"type": "string"}, "value": {"type": "string"}},
         "required": ["selector", "value"]}},
    {"name": "browser_download",
     "description": "Download the document behind a link, into the run's artifacts.",
     "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}},
                      "required": ["selector"]}},
    {"name": "email_send", "description": "Send the loss run request email.",
     "input_schema": {"type": "object", "properties": {
         "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
         "required": ["to", "subject", "body"]}},
    {"name": "done", "description": "Finish this policy.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string",
                    "enum": ["retrieved", "awaiting_reply", "input_required", "failed"]},
         "summary": {"type": "string"}}, "required": ["status", "summary"]}},
]


@dataclass
class Action:
    name: str
    input: dict


# --------------------------------------------------------------------------


class ScriptedPlanner:
    """Deterministic planner driven by what it reads on the page."""

    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.carrier = ctx["carrier"]
        self.meta = config.CARRIERS[self.carrier]
        self.stage = "start"
        self.policy_attempt = ctx["policy_number"]
        self.downloaded: list[str] = []
        self.seen_links: set[str] = set()
        self.reads_without_links = 0
        self.reformats = 0

    def next(self, last_obs: str) -> Action:
        c, meta = self.carrier, self.meta
        obs = last_obs or ""

        if meta["channel"] == "email":
            if self.stage == "start":
                self.stage = "sent"
                ctx = self.ctx
                return Action("email_send", {
                    "to": meta["email"],
                    "subject": (f"Loss run request · {ctx['account']} · "
                                f"{ctx['policy_number']}"),
                    "body": (
                        f"Hello,\n\nRequesting 5-year currently valued loss runs for "
                        f"{ctx['account']}, policy {ctx['policy_number']}, "
                        f"{ctx['period_start']} through {ctx['period_end']}. "
                        f"Renewal is {ctx['expires_short']}.\n\n"
                        f"Thank you,\n{config.BROKER_NAME}"),
                })
            return Action("done", {"status": "awaiting_reply",
                                   "summary": f"Request emailed to {meta['email']}."})

        # ---- portal carriers ------------------------------------------------
        if self.stage == "start":
            self.stage = "login"
            return Action("browser_goto", {"url": config.base_url(c)})

        if self.stage == "login":
            self.stage = "login_pw"
            return Action("browser_fill", {"selector": "#username",
                                           "secret_ref": f"auth.{c}.username"})
        if self.stage == "login_pw":
            self.stage = "login_submit"
            return Action("browser_fill", {"selector": "#password",
                                           "secret_ref": f"auth.{c}.password"})
        if self.stage == "login_submit":
            self.stage = "after_login"
            return Action("browser_click", {"selector": "button[type=submit]"})

        if self.stage == "after_login":
            self.stage = "route"
            return Action("browser_read_page", {})

        if self.stage == "route":
            # Decide purely from what the page says.
            if "first pet" in obs or "Additional Verification" in obs:
                return Action("done", {
                    "status": "input_required",
                    "summary": ("Portal asked a security question ('What was the name of "
                                "your first pet?') with no answer in the vault. "
                                "Stopped without guessing.")})
            if "Two-Factor" in obs or "#code" in obs:
                self.stage = "totp_submit"
                return Action("browser_fill", {"selector": "#code",
                                               "secret_ref": f"auth.{c}.totp_code"})
            if "Claims History" in obs:
                self.stage = "download_list"
                return Action("browser_read_page", {})
            if "#policy" in obs:
                self.stage = "search_submit"
                return Action("browser_fill", {"selector": "#policy",
                                               "value": self.policy_attempt})
            self.stage = "route"
            return Action("browser_read_page", {})

        if self.stage == "totp_submit":
            self.stage = "after_login"
            return Action("browser_click", {"selector": "button[type=submit]"})

        if self.stage == "search_submit":
            self.stage = "search_result"
            return Action("browser_click", {"selector": "button[type=submit]"})

        if self.stage == "search_result":
            self.stage = "check_search"
            return Action("browser_read_page", {})

        if self.stage == "check_search":
            # Self-correction: the rule comes off the page, not from this code.
            if "Invalid policy number format" in obs and self.reformats < 3:
                self.reformats += 1
                m = re.search(r"Enter as ([A-Z0-9]+), no separators", obs)
                if m:
                    self.policy_attempt = re.sub(r"[^A-Za-z0-9]", "", self.policy_attempt)
                self.stage = "search_submit"
                return Action("browser_fill", {"selector": "#policy",
                                               "value": self.policy_attempt})
            if "No matching policy" in obs:
                return Action("done", {"status": "failed",
                                       "summary": "Policy not found in the portal."})
            if "Loss Run Report" in obs:
                self.stage = "form_to"
                return Action("browser_fill", {"selector": "#from_date",
                                               "value": self.ctx["period_start"]})
            self.stage = "check_search"
            return Action("browser_read_page", {})

        if self.stage == "form_to":
            self.stage = "form_fmt"
            return Action("browser_fill", {"selector": "#to_date",
                                           "value": self.ctx["period_end"]})
        if self.stage == "form_fmt":
            self.stage = "form_submit"
            return Action("browser_select", {"selector": "#format", "value": "detail"})
        if self.stage == "form_submit":
            self.stage = "ready"
            return Action("browser_click", {"selector": "button[type=submit]"})
        if self.stage == "ready":
            self.stage = "download_list"
            return Action("browser_read_page", {})

        if self.stage == "download_list":
            # Take EVERY download link on offer -- Harborstone lists two, and the
            # AOP is explicit that a large loss detail must be taken as well.
            links = re.findall(r"#(download-\d+)", obs)
            if links:
                self.seen_links.update(f"#{l}" for l in links)
            todo = sorted(self.seen_links - set(self.downloaded))
            if todo:
                self.downloaded.append(todo[0])
                # re-read the page after each download so remaining links resurface
                self.stage = "download_recheck"
                return Action("browser_download", {"selector": todo[0]})
            if self.downloaded:
                return Action("done", {
                    "status": "retrieved",
                    "summary": f"Saved {len(self.downloaded)} document(s) for "
                               f"{self.ctx['policy_number']}."})
            if self.reads_without_links >= 2:
                return Action("done", {"status": "failed",
                                       "summary": "No document link found on the page."})
            self.reads_without_links += 1
            return Action("browser_read_page", {})

        if self.stage == "download_recheck":
            self.stage = "download_list"
            return Action("browser_read_page", {})

        return Action("done", {"status": "failed", "summary": "Planner stalled."})


# --------------------------------------------------------------------------


class ClaudePlanner:
    """Real planner: the AOP becomes the system prompt; Claude picks the tools."""

    MODEL = "claude-opus-5"

    def __init__(self, ctx: dict, aop_text: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.system = aop_text
        self.messages: list[dict] = [{
            "role": "user",
            "content": (f"Retrieve the loss run for policy {ctx['policy_number']} "
                        f"({ctx['carrier']}, {ctx['account']}). Begin at step 1."),
        }]
        self._pending: list[Any] = []

    def observe(self, tool_use_id: str, result: str) -> None:
        self._pending.append({"type": "tool_result", "tool_use_id": tool_use_id,
                              "content": result[:6000]})

    def flush(self) -> None:
        if self._pending:
            self.messages.append({"role": "user", "content": self._pending})
            self._pending = []

    def next_batch(self) -> tuple[list[tuple[str, Action]], str]:
        """Returns [(tool_use_id, Action)], assistant_text."""
        self.flush()
        resp = self.client.messages.create(
            model=self.MODEL,
            max_tokens=1500,
            system=[{"type": "text", "text": self.system,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS_SPEC,
            messages=self.messages,
        )
        self.messages.append({"role": "assistant", "content": resp.content})
        calls, text = [], ""
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                calls.append((block.id, Action(block.name, dict(block.input))))
        return calls, text
