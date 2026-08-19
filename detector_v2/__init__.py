"""Detector V2: outcome-oriented hallucination detection.

Independent package. It does NOT import anything from ``sehd_rise2``.

Architecture (two stages):

    packet -> judge (recall) -> defense (provable overturn only) -> adjudication

The package is deliberately small. Self-evolution extension points are:

- ``interventions``: the single deployment plane — a validated
  ``detector_v2.intervention_registry.v1`` payload whose three closed
  intervention types execute only through registered hooks;
- structured outputs ``judge.delivered_result / correct_result / basis``
  and ``defense.verdict / evidence_refs``;
- three-valued authority ``authoritative / degraded_judge_only / invalid``
  so a defense-layer failure never turns a true positive into a miss.
"""

from __future__ import annotations

from detector_v2.api import Detector, detect

__version__ = "0.1.0"

__all__ = ["Detector", "detect", "__version__"]
