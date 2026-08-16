"""SEHD-RISE2 minimal detector package.

A from-scratch, minimal reimplementation of the hallucination-detector
essence (census -> verification -> adversarial defense -> deterministic
court). The package root exports nothing: applications use
``sehd_rise2.detector.api``; the evolution framework imports the detector
surfaces it needs (artifacts, result readers, court) directly — one-way,
enforced by ``tools/check_layers.py``.
"""
