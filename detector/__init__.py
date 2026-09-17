"""Outcome-oriented hallucination detector.

Packet -> judge -> optional recall/causal upgrade -> tracer/defense hearings
-> deterministic adjudication. Four intervention types execute through the
validated registry. This package has no dependency on the evolution package.
"""

from __future__ import annotations

from detector.api import Detector, detect

__version__ = "0.1.0"

__all__ = ["Detector", "detect", "__version__"]
