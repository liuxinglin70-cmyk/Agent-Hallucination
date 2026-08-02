"""Lazy public facade for the core packages.

The detector is a standalone runtime.  Imports from BCCSE and HTC-TES are
resolved only when those names are requested, so importing
``sehd_v6.core.paca_tcl`` does not load experiment orchestration or evolution.
"""

_MODULE_EXPORTS = {
    "run_online_evolution": ("bccse", "run_online_evolution"),
    "SkillGenome": ("bccse", "SkillGenome"),
    "create_baseline_genome": ("bccse", "create_baseline_genome"),
    "ErrorType": ("bccse", "ErrorType"),
    "attribute_failure": ("bccse", "attribute_failure"),
    "AttributionSignal": ("bccse", "AttributionSignal"),
    "SkillEdit": ("bccse", "SkillEdit"),
    "SkillOp": ("bccse", "SkillOp"),
    "apply_edit": ("bccse", "apply_edit"),
    "validate_edit": ("bccse", "validate_edit"),
    "StructuralGate": ("bccse", "StructuralGate"),
    "create_structural_gate": ("bccse", "create_structural_gate"),
    "adjudicate_case": ("paca_tcl", "adjudicate_case"),
    "evaluate": ("paca_tcl", "evaluate"),
    "HallucinationType": ("htc_tes", "HallucinationType"),
    "HallucinationTypeClassifier": ("htc_tes", "HallucinationTypeClassifier"),
    "TypeClassificationResult": ("htc_tes", "TypeClassificationResult"),
    "TypeConditionedEvolution": ("htc_tes", "TypeConditionedEvolution"),
    "TypeAwareFitness": ("htc_tes", "TypeAwareFitness"),
    "classify_hallucination_type": ("htc_tes", "classify_hallucination_type"),
}


def __getattr__(name: str):
    import importlib

    try:
        module_name, attribute = _MODULE_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(importlib.import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


__all__ = sorted(_MODULE_EXPORTS)
