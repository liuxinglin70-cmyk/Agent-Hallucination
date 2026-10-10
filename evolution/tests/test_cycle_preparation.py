from dataclasses import asdict
from pathlib import Path

import pytest

from evolution.backends import MockDetectorBackend
from evolution.engine import EvolutionEngine
from evolution.proposer import ProposalBatch
from evolution.splits import EvolutionSplit
from evolution.tests.test_engine_smoke import _config, _make_dataset


class EmptyProposer:
    name = "empty_fixture"

    def propose(self, families, *, cycle, max_proposals):
        return ProposalBatch(candidates=[], joint_groups=[], notes=[])


@pytest.mark.parametrize("has_failure", [False, True])
def test_preview_and_cycle_share_preparation_without_preview_commits(tmp_path, monkeypatch, has_failure):
    import evolution.engine as module

    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    config["budget"]["patrol_cases"] = 8 if has_failure else 4
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4], gain=cases[4:12],
                           patrol_pool=cases[12:], mini=True)
    wrong = [case for case in cases[12:] if int(Path(case).stem) % 2 == 0]
    backend = MockDetectorBackend(base_predictions={case: False for case in wrong} if has_failure else {})
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True, proposer=EmptyProposer())
    patrolled = []
    prepared = []
    real_patrol, real_families = module.prepare_patrol, module.prepare_families

    def patrol(**kwargs):
        result = real_patrol(**kwargs)
        patrolled.append((result.cases, result.bootstrap_cases, result.contrast))
        return result

    def families(*args, **kwargs):
        result = real_families(*args, **kwargs)
        prepared.append(asdict(result))
        return result

    monkeypatch.setattr(module, "prepare_patrol", patrol)
    monkeypatch.setattr(module, "prepare_families", families)
    ledger_path = Path(config["ledger_path"])
    before_ledger = ledger_path.read_bytes() if ledger_path.exists() else None
    registry = Path(config["registry_dir"])
    before_registry = {str(path.relative_to(registry)): path.read_bytes()
                       for path in registry.rglob("*") if path.is_file()}
    preview = engine.preview_cycle(1)
    assert (ledger_path.read_bytes() if ledger_path.exists() else None) == before_ledger
    assert {str(path.relative_to(registry)): path.read_bytes()
            for path in registry.rglob("*") if path.is_file()} == before_registry
    assert bool(preview["families"]) is has_failure
    assert (preview["bootstrap_count"] > 0) is (not has_failure)
    engine.run_cycle(1)
    assert len(prepared) == len(patrolled) == 2
    assert prepared[0] == prepared[1]
    assert patrolled[0] == patrolled[1]
