"""
engine — document extraction and assembly, independent of any UI.

Pure Python: no Flask imports anywhere in this package. A dashboard, a CLI, a
scheduled job or a test can all call it the same way, and the accuracy harness
in `engine/eval/` grades it without a request context.

Modules
-------
loss_run  Carrier binder → insured, policy number, carrier, effective date,
          coverage. Layout-mode text, scored page selection, scored field
          candidates. Graded at 99.5% over 80 binders; see engine/eval/.
rsm       RSM deck assembly. Cross-presentation slide copy at the ZIP level,
          because python-pptx has no correct one.
base      SkillResult — the uniform return shape every entry point uses.
"""
from __future__ import annotations

from .base import SkillResult

__all__ = ["SkillResult", "loss_run", "rsm"]
