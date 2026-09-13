"""Start / stop / health-check the mock world."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

import config

PID_FILE = config.ROOT / ".services.json"


def _targets() -> list[tuple[str, list[str], int]]:
    py = sys.executable
    out = [("epic", [py, "mock_epic.py"], config.EPIC_PORT)]
    for name, meta in config.CARRIERS.items():
        if meta["channel"] == "portal":
            out.append((name, [py, "mock_portals.py", name], meta["port"]))
    return out


def _alive(port: int) -> bool:
    try:
        httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
        return True
    except Exception:
        return False


def ensure_vault() -> None:
    if not config.VAULT_PATH.exists():
        shutil.copy(config.VAULT_PATH.with_name("vault.example.json"), config.VAULT_PATH)


def start(wait: float = 25.0) -> dict:
    config.ensure_dirs()
    ensure_vault()
    if not (config.SEED_PDF_DIR / "NBM_LossRun_2021-2025.pdf").exists():
        import seed
        seed.generate_all()

    procs = {}
    for name, cmd, port in _targets():
        if _alive(port):
            procs[name] = {"port": port, "pid": None, "reused": True}
            continue
        p = subprocess.Popen(cmd, cwd=config.ROOT,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs[name] = {"port": port, "pid": p.pid, "reused": False}

    deadline = time.time() + wait
    pending = {n: d["port"] for n, d in procs.items()}
    while pending and time.time() < deadline:
        for n in list(pending):
            if _alive(pending[n]):
                del pending[n]
        if pending:
            time.sleep(0.4)

    PID_FILE.write_text(json.dumps(procs, indent=2))
    if pending:
        raise RuntimeError(f"services failed to start: {sorted(pending)}")
    return procs


def stop() -> None:
    if not PID_FILE.exists():
        return
    procs = json.loads(PID_FILE.read_text())
    for name, d in procs.items():
        pid = d.get("pid")
        if not pid:
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, check=False)
            else:
                import os
                import signal
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    PID_FILE.unlink(missing_ok=True)


def status() -> dict:
    return {name: _alive(port) for name, _, port in _targets()}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        for n, d in start().items():
            print(f"  {n:14} :{d['port']}  {'(reused)' if d['reused'] else 'started'}")
    elif cmd == "stop":
        stop()
        print("stopped")
    else:
        for n, ok in status().items():
            print(f"  {n:14} {'UP' if ok else 'down'}")
