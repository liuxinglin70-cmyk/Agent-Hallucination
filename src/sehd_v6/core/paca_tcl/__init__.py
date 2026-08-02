"""Standalone PACA-TCL detector public facade.

This module deliberately uses lazy exports.  The core detector path imports
only packet, transport, and proof-court code; BCCSE skill integration is loaded
only when an integration symbol is explicitly requested.
"""

_EXPORTS = {
    "adjudicate_case": ("proof_engine", "adjudicate_case"),
    "evaluate": ("proof_engine", "evaluate"),
    "detect_record": ("detector", "detect_record"),
    "load_detector_config": ("detector", "load_detector_config"),
    "EvolutionDetection": ("evolution_contract", "EvolutionDetection"),
    "project_for_evolution": ("evolution_contract", "project_for_evolution"),
    "PacaStage": ("skill_integration", "PacaStage"),
    "PACA_MODULE_STAGE": ("skill_integration", "PACA_MODULE_STAGE"),
    "compile_genome_for_paca": ("skill_integration", "compile_genome_for_paca"),
    "integrated_v35_provider": ("skill_integration", "integrated_v35_provider"),
    "write_json": ("basic_runner", "write_json"),
    "write_jsonl": ("basic_runner", "write_jsonl"),
    "read_json": ("basic_runner", "read_json"),
}


def __getattr__(name: str):
    import importlib

    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(importlib.import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


__all__ = sorted(_EXPORTS)
