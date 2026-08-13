"""Cross-layer audit for the Skill capability semantic closure.

The registry lives in the lowest owning layer.  This loop-layer audit is the
only place that joins that declaration to both deterministic executors and the
fitness postcondition implementations without violating dependency direction.
"""

from __future__ import annotations

from sehd_rise.detection.policy import (
    EXECUTED_L2_REPAIR_OPERATIONS,
    L2_FITNESS_POSTCONDITIONS,
)
from sehd_rise.execution.action import POSTCONDITION_CHECKS
from sehd_rise.skills.capabilities import (
    L1_COURT_TRANSFORM_EXECUTOR,
    L2_ADJUDICATION_POLICY_EXECUTOR,
    SKILL_CAPABILITY_REGISTRY,
)
from sehd_rise.skills.l1_execution import EXECUTED_L1_OPERATIONS


def validate_semantic_closure() -> tuple[str, ...]:
    """Return every registry -> executor -> fitness contract break."""
    issues: list[str] = []
    for capability in SKILL_CAPABILITY_REGISTRY.all():
        operation = capability.executable_operation
        if capability.executor == L1_COURT_TRANSFORM_EXECUTOR:
            if operation not in EXECUTED_L1_OPERATIONS:
                issues.append(f"executor_missing:{operation}")
            if capability.fitness_postcondition not in POSTCONDITION_CHECKS:
                issues.append(f"fitness_postcondition_missing:{operation}")
        elif capability.executor == L2_ADJUDICATION_POLICY_EXECUTOR:
            if operation not in EXECUTED_L2_REPAIR_OPERATIONS:
                issues.append(f"executor_missing:{operation}")
            if capability.fitness_postcondition not in L2_FITNESS_POSTCONDITIONS:
                issues.append(f"fitness_postcondition_missing:{operation}")
        else:
            issues.append(f"executor_unknown:{operation}:{capability.executor}")
    return tuple(issues)


__all__ = ["validate_semantic_closure"]
