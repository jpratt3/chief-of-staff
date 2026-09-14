"""
dashboard_v2/assistant.py

Backs the "Ask" bar on the overview page.

The model is given a compact snapshot of the current book — every client, the
stage it sits in, its renewal date, and which stage tasks are outstanding — and
answers questions against it. There is no retrieval step: the whole book is a
few thousand tokens, so it goes in the system prompt behind a cache breakpoint
and is served from cache on every request after the first.

Credentials come from the environment (ANTHROPIC_API_KEY, or an `ant auth
login` profile). Nothing is read from, or written to, the repo — and if no
credential is configured the caller degrades to a static message rather than
raising, so a fresh clone still runs.
"""
from __future__ import annotations

import json
import os
from typing import Iterator

from data_loader import (
    STAGES,
    get_current_stage,
    load_clients,
    load_completions,
    load_stage_responses,
    load_tasks,
)

MODEL = "claude-opus-5"
MAX_TOKENS = 4000

SYSTEM_PREAMBLE = """You are the assistant inside "Chief of Staff", a workflow tool an \
insurance broker uses to run commercial property & casualty renewals.

A renewal moves through seven stages in order: Renewal Preparation, RSM (renewal \
strategy meeting), Submission, Proposal, Bind, Invoice, Post Binding. Each stage has a \
numbered task list. A client sits in exactly one stage at a time, and tasks complete in \
order, so the outstanding tasks are always a suffix of the stage's list.

Answer questions about the book below: what is due, what is behind, who sits where, what \
is outstanding. Be direct and specific — name clients and dates. Prefer a short answer; \
use a compact list when the question covers several clients. Never invent a client, a \
date, or a task that is not in the data. If the data cannot answer the question, say so \
in one sentence.

Today's date and the full book follow. Treat them as the only source of truth."""


def is_configured() -> bool:
    """True when a usable credential is available for the Anthropic SDK.

    A non-empty value is not enough. Copying .env.example to .env leaves the
    placeholder in place, which is truthy, so the request went out and came back
    a 401 — an authentication error for what is really an unconfigured install.
    Anything that is not shaped like a key is treated as absent.
    """
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if key.startswith("sk-ant-") and len(key) > 40:
        return True
    # OAuth tokens come from `ant auth login` and have no fixed prefix.
    token = (os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    return len(token) > 40


def build_book_snapshot() -> str:
    """The whole book as compact JSON — the cached prefix of every request."""
    from datetime import date

    clients = load_clients()
    responses = load_stage_responses()
    completions = load_completions()
    tasks = load_tasks()

    catalog = {
        s: {
            "window": tasks.get(s, {}).get("window", ""),
            "tasks": [
                {"num": t["num"], "task": t["text"]}
                for t in tasks.get(s, {}).get("tasks", [])
            ],
        }
        for s in STAGES
    }

    book = []
    for c in clients:
        name = c["display_name"]
        stage = get_current_stage(c, responses)
        primary = c["primary"]
        done_map = completions.get(name, {}).get(stage, {})
        done = sorted(int(k) for k, v in done_map.items() if v)
        stage_tasks = tasks.get(stage, {}).get("tasks", [])
        outstanding = [t["num"] for t in stage_tasks if t["num"] not in done]

        team = {}
        for role, val in (c.get("team") or {}).items():
            if isinstance(val, dict) and val.get("name"):
                team[role] = val["name"]

        book.append({
            "client": name,
            "stage": stage,
            "renewal_date": primary.get("renewal_date", ""),
            "stage_due": primary.get("stage_dates", {}).get(stage, ""),
            "other_programs": c.get("secondary_note", ""),
            "tasks_done": done,
            "tasks_outstanding": outstanding,
            "team": team,
        })

    return json.dumps(
        {
            "today": date.today().strftime("%m/%d/%Y"),
            "stage_order": STAGES,
            "task_catalog": catalog,
            "book": book,
        },
        indent=1,
        ensure_ascii=False,
    )


MAX_TURNS = 12          # question/answer pairs carried forward
MAX_TURN_CHARS = 6000   # per message, so one long paste cannot blow up context


def clean_history(raw) -> list[dict]:
    """Validate client-supplied history into an alternating message list.

    The transcript round-trips through the browser, so treat it as untrusted:
    keep only well-formed user/assistant turns, cap their length, and keep the
    most recent ones.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()[:MAX_TURN_CHARS]
        if content:
            out.append({"role": role, "content": content})
    # A turn is a pair, and the model needs the list to start on a user message.
    out = out[-(MAX_TURNS * 2):]
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def stream_answer(question: str, history: list[dict] | None = None) -> Iterator[str]:
    """Yield the answer in chunks. Raises nothing the caller must handle."""
    import anthropic

    client = anthropic.Anthropic()
    system = [
        {
            "type": "text",
            "text": SYSTEM_PREAMBLE + "\n\n" + build_book_snapshot(),
            # The book is identical across requests, so everything above this
            # breakpoint is served from cache after the first call.
            "cache_control": {"type": "ephemeral"},
        }
    ]

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        thinking={"type": "adaptive"},
        # Lookups over a small book don't need deep reasoning, and the bar
        # should feel immediate.
        output_config={"effort": "low"},
        # Prior turns sit after the cached system block, so the book is served
        # from cache and only the conversation itself is fresh input.
        messages=(history or []) + [{"role": "user", "content": question}],
    ) as stream:
        for text in stream.text_stream:
            yield text
