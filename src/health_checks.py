from __future__ import annotations

from pathlib import Path


def run_startup_checks(
    root_path: str,
    log_dir: str,
    state_path: str,
    settings_path: str,
) -> list[str]:
    """
    Run lightweight local startup checks and return a list of issues.
    Empty list means checks passed.
    """
    issues: list[str] = []

    if not Path(root_path).exists():
        issues.append(f"Root path does not exist: {root_path}")

    if not Path(settings_path).exists():
        issues.append(f"Settings file does not exist: {settings_path}")

    state_parent = Path(state_path).parent
    if not state_parent.exists():
        issues.append(f"State file parent directory does not exist: {state_parent}")

    try:
        import win32com.client  # noqa: F401
    except ImportError:
        issues.append("pywin32 is not installed or win32com.client is unavailable.")

    return issues
