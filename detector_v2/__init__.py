"""Detector V2: outcome-oriented hallucination detection.

Independent package. It does NOT import anything from ``sehd_rise2``.

Architecture (two stages):

    packet -> judge (recall) -> defense (provable overturn only) -> adjudication

The package is deliberately small. Self-evolution extension points are:

- ``artifacts``: validated ``prompt_directive`` registry with mechanical
  triggers (the only deployable artifact kind for now);
- structured outputs ``judge.delivered_result / correct_result / basis``
  and ``defense.verdict / evidence_refs``;
- three-valued authority ``authoritative / degraded_judge_only / invalid``
  so a defense-layer failure never turns a true positive into a miss.
"""

from __future__ import annotations

from detector_v2.api import Detector, detect

__version__ = "0.1.0"

__all__ = ["Detector", "detect", "__version__"]
