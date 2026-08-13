"""Run-mode tri-section: three explicit evolution regimes (§10).

The public API previously collapsed three distinct regimes into a tangle of
``use_gold`` / ``fitness_use_gold`` / ``canary_case_ids`` defaults, so a
run could not say whether "no skill committed" meant "the planner failed" or
"the evidence authority was absent and commit was rightly refused". The three
modes make that explicit:

    supervised_evolution            gold dev supervision + canary + holdout;
                                    commits may be claimed.
    label_free_diagnostic           generate hypotheses / experience but never
                                    claim a learning success; commit is refused
                                    by construction (no correctness authority).
    protected_label_free_adaptation label-free, but an external / canary
                                    correctness authority is present, so
                                    directional commits are admitted as
                                    provisional adaptation — never as a
                                    claimed learning success.

``resolve_run_mode`` is the single authority that maps a declared mode onto the
underlying flags, enforces its prerequisites fail-fast, and derives the
``claim_learning`` semantic the result records. The shadow-batch admission
predicate (``loop.shadow._shadow_commit_eligible``) already encodes the
canary-vs-gold distinction; this module only makes the regime explicit and
validated, never re-deriving the commit rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence


class RunMode(str, Enum):
    SUPERVISED_EVOLUTION = "supervised_evolution"
    LABEL_FREE_DIAGNOSTIC = "label_free_diagnostic"
    PROTECTED_LABEL_FREE_ADAPTATION = "protected_label_free_adaptation"


@dataclass(frozen=True)
class RunModeResolution:
    """Resolved flags and claim semantics for one declared run mode."""

    run_mode: str
    use_gold: bool
    fitness_use_gold: bool
    gold_reflection: bool
    claim_learning: bool
    requires_canary: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_mode": self.run_mode,
            "use_gold": self.use_gold,
            "fitness_use_gold": self.fitness_use_gold,
            "gold_reflection": self.gold_reflection,
            "claim_learning": self.claim_learning,
            "requires_canary": self.requires_canary,
        }


def resolve_run_mode(
    run_mode: str,
    *,
    use_gold: bool,
    fitness_use_gold: bool,
    gold_reflection: bool,
    canary_case_ids: Sequence[str] = (),
) -> RunModeResolution:
    """Resolve a declared mode onto flags, enforcing prerequisites fail-fast.

    Unknown mode strings fail closed. Explicit flag overrides are honored for
    the supervised / protected regimes (the caller may already have resolved
    gold or canary sources); the diagnostic regime is forced label-free.
    """
    mode = str(run_mode or "")
    if mode == RunMode.SUPERVISED_EVOLUTION.value:
        resolved = RunModeResolution(
            run_mode=mode,
            use_gold=True,
            fitness_use_gold=True,
            gold_reflection=bool(gold_reflection),
            claim_learning=True,
            requires_canary=False,
        )
    elif mode == RunMode.LABEL_FREE_DIAGNOSTIC.value:
        resolved = RunModeResolution(
            run_mode=mode,
            use_gold=False,
            fitness_use_gold=False,
            gold_reflection=False,
            claim_learning=False,
            requires_canary=False,
        )
    elif mode == RunMode.PROTECTED_LABEL_FREE_ADAPTATION.value:
        # A canary can prove process non-regression, never that a newly
        # decisive verdict is correct, so a label-free directional commit is
        # provisional adaptation — the run records no learning success until
        # an external authority verifies it.
        resolved = RunModeResolution(
            run_mode=mode,
            use_gold=False,
            fitness_use_gold=False,
            gold_reflection=False,
            claim_learning=False,
            requires_canary=True,
        )
    else:
        raise ValueError(
            f"unknown run_mode: {mode!r}; expected one of "
            f"{[m.value for m in RunMode]}"
        )

    if resolved.requires_canary and not tuple(canary_case_ids):
        raise ValueError(
            "protected_label_free_adaptation requires canary_case_ids: an "
            "external correctness authority must vouch for any commit"
        )
    return resolved


__all__ = [
    "RunMode",
    "RunModeResolution",
    "resolve_run_mode",
]
