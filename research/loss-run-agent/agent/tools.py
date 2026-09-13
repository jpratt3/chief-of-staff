"""Tool layer. The executor and every planner talk to the world only through this.

Two rules matter here:
  1. Secrets are referenced by name (`auth.Redstone.password`) and resolved INSIDE
     the tool. A secret value never enters planner context and never reaches the trace.
  2. Every call is traced, with a screenshot for browser actions.

The browser tools are deliberately generic (goto / read_page / fill / click /
select / download) so a computer-use executor could implement the same names
against pixels instead of the DOM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

import config

SECRET_KEYS = ("password", "totp_secret", "secret", "security_answer")


def load_vault() -> dict:
    return json.loads(config.VAULT_PATH.read_text())


def resolve_secret(ref: str) -> str | None:
    """`auth.Northbridge.password` -> the value, or None if absent."""
    if not ref.startswith("auth."):
        return None
    _, carrier, key = ref.split(".", 2)
    entry = load_vault().get(carrier, {})
    if key == "totp_code":  # derived, not stored
        import pyotp
        secret = entry.get("totp_secret")
        return pyotp.TOTP(secret).now() if secret else None
    return entry.get(key)


def vault_has(ref: str) -> bool:
    return resolve_secret(ref) is not None


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in SECRET_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


@dataclass
class Trace:
    run_dir: Path
    step: str = "start"
    _n: int = 0
    events: list[dict] = field(default_factory=list)

    def record(self, tool: str, args: dict, result: str, screenshot: str | None = None,
               note: str = "") -> None:
        self._n += 1
        ev = {
            "seq": self._n,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "step": self.step,
            "tool": tool,
            "args": _redact(args),
            "result": result[:600],
            "screenshot": screenshot,
            "note": note,
        }
        self.events.append(ev)
        with (self.run_dir / "trace.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")


class BrowserTools:
    """Playwright-backed browser. One context per run."""

    def __init__(self, trace: Trace, headless: bool = True):
        self.trace = trace
        self.headless = headless
        self._pw = None
        self._browser = None
        self.page = None
        self.shots = trace.run_dir / "artifacts" / "screens"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.artifacts = trace.run_dir / "artifacts"

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        ctx = await self._browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = await ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def _shot(self, label: str) -> str:
        name = f"{self.trace._n + 1:03d}-{re.sub(r'[^a-z0-9]+', '-', label.lower())[:40]}.jpeg"
        try:
            await self.page.screenshot(path=str(self.shots / name), quality=55, type="jpeg")
        except Exception:
            return ""
        return f"screens/{name}"

    # -- tools ------------------------------------------------------------
    async def goto(self, url: str) -> str:
        await self.page.goto(url, wait_until="domcontentloaded")
        shot = await self._shot("goto")
        out = f"Loaded {self.page.url}"
        self.trace.record("browser_goto", {"url": url}, out, shot)
        return out

    async def read_page(self) -> str:
        """Visible text + actionable elements. This is the agent's 'screen'."""
        text = await self.page.evaluate(
            "() => document.body.innerText.replace(/\\n{2,}/g,'\\n').trim()")
        fields = await self.page.evaluate("""() => {
            const out=[];
            document.querySelectorAll('input,select,textarea').forEach(el=>{
              if(el.type==='hidden') return;
              const sel = el.id ? '#'+el.id : (el.name ? `[name="${el.name}"]` : el.tagName.toLowerCase());
              let opts='';
              if(el.tagName==='SELECT') opts=' options=['+[...el.options].map(o=>o.value).join('|')+']';
              out.push(`${sel} (${el.tagName.toLowerCase()}${el.type?':'+el.type:''})${opts}`);
            });
            return out;
        }""")
        controls = await self.page.evaluate("""() => {
            const out=[];
            document.querySelectorAll('button,a[href],input[type=submit]').forEach(el=>{
              const sel = el.id ? '#'+el.id : null;
              const label=(el.innerText||el.value||'').trim().slice(0,40);
              if(label) out.push(`${sel||el.tagName.toLowerCase()} "${label}"`);
            });
            return out;
        }""")
        obs = (f"URL: {self.page.url}\n--- text ---\n{text}\n"
               f"--- fields ---\n" + "\n".join(fields) +
               "\n--- controls ---\n" + "\n".join(controls))

        # Surface any on-screen notice in the trace line -- this is what the
        # planner reacts to, so it is the interesting part of the observation.
        notice = ""
        for line in text.split("\n"):
            if re.search(r"invalid|not valid|does not match|no matching|required|"
                         r"generated in|verification", line, re.I):
                notice = line.strip()
                break
        summary = f"{self.page.url}" + (f"  —  {notice}" if notice else "")
        self.trace.record("browser_read_page", {}, summary, None, note=notice)
        return obs

    async def fill(self, selector: str, value: str | None = None,
                   secret_ref: str | None = None) -> str:
        if secret_ref:
            real = resolve_secret(secret_ref)
            if real is None:
                out = f"NOT AVAILABLE: {secret_ref} is not in the vault."
                self.trace.record("browser_fill",
                                  {"selector": selector, "secret_ref": secret_ref}, out)
                return out
            await self.page.fill(selector, real)
            out = f"Filled {selector} from {secret_ref}"
            self.trace.record("browser_fill",
                              {"selector": selector, "secret_ref": secret_ref}, out)
            return out
        await self.page.fill(selector, value or "")
        out = f"Filled {selector} with '{value}'"
        self.trace.record("browser_fill", {"selector": selector, "value": value}, out)
        return out

    async def click(self, selector: str) -> str:
        try:
            await self.page.click(selector, timeout=5000)
            await self.page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            out = f"Click failed on {selector}: {type(e).__name__}"
            self.trace.record("browser_click", {"selector": selector}, out)
            return out
        shot = await self._shot(f"click {selector}")
        out = f"Clicked {selector}; now at {self.page.url}"
        self.trace.record("browser_click", {"selector": selector}, out, shot)
        return out

    async def select(self, selector: str, value: str) -> str:
        await self.page.select_option(selector, value)
        out = f"Selected '{value}' in {selector}"
        self.trace.record("browser_select", {"selector": selector, "value": value}, out)
        return out

    async def download(self, selector: str) -> str:
        """Resolve the element's href and save the bytes into the run's artifacts."""
        href = await self.page.get_attribute(selector, "href")
        if not href:
            out = f"No href on {selector}"
            self.trace.record("browser_download", {"selector": selector}, out)
            return out
        url = href if href.startswith("http") else self.page.url.split("/", 3)[0] + "//" + \
            self.page.url.split("/", 3)[2] + href
        resp = await self.page.request.get(url)
        body = await resp.body()
        name = url.rsplit("/", 1)[-1]
        path = self.artifacts / name
        path.write_bytes(body)
        out = f"Downloaded {name} ({len(body)//1024} KB) to artifacts/"
        self.trace.record("browser_download", {"selector": selector, "file": name}, out)
        return out


class EpicTools:
    def __init__(self, trace: Trace):
        self.trace = trace

    def get_renewal(self, account_id: str) -> dict:
        r = httpx.get(f"{config.epic_url()}/accounts/{account_id}", timeout=10)
        r.raise_for_status()
        data = r.json()
        self.trace.record("epic_get_renewal", {"account_id": account_id},
                          f"{data['account']} - {len(data['policies'])} policies")
        return data

    def post_attachment(self, account_id: str, name: str, size_kb: int, note: str = "") -> dict:
        r = httpx.post(f"{config.epic_url()}/accounts/{account_id}/attachments",
                       json={"name": name, "size_kb": size_kb, "note": note}, timeout=10)
        self.trace.record("epic_post_attachment",
                          {"account_id": account_id, "name": name}, f"attached {name}")
        return r.json()

    def post_activity(self, account_id: str, text: str) -> dict:
        r = httpx.post(f"{config.epic_url()}/accounts/{account_id}/activities",
                       json={"text": text}, timeout=10)
        self.trace.record("epic_post_activity", {"account_id": account_id}, text[:120])
        return r.json()
