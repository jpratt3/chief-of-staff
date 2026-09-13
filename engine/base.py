"""
engine/base.py
Shared contract for all skill service functions.
No Flask imports — pure Python.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class SkillResult:
    """
    Uniform return type for all skill service functions.

    success : bool   — True if processing succeeded
    data    : dict   — output payload (skill-specific keys)
    error   : str    — human-readable error message if success=False
    """
    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""
