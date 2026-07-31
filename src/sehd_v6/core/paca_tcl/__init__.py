"""PACA-TCL v3.5 — Court-style causal attribution detector.

Public API for the frozen detection base.
Import from this package rather than individual modules.
"""

from .proof_engine import adjudicate_case, evaluate
from .skill_integration import (
    PacaStage,
    MODULE_STAGE,
    compile_genome_for_paca,
    integrated_v35_provider,
)
from .basic_runner import write_json, write_jsonl, read_json

__all__ = [
    # Engine
    "adjudicate_case",
    "evaluate",
    # Integration
    "PacaStage",
    "MODULE_STAGE",
    "compile_genome_for_paca",
    "integrated_v35_provider",
    # I/O
    "write_json",
    "write_jsonl",
    "read_json",
]
