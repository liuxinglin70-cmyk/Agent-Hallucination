"""Self-evolution layer: reproduced failures -> gated artifacts.

The detector package is frozen; everything that evolves lives here.
Layer rule (enforced by ``tools/check_layers.py``): evolution imports
detector, never the reverse. The package intentionally exports
nothing — round orchestration lives in
``experiments/run_fixval_round.py`` and composes the modules directly.
"""
