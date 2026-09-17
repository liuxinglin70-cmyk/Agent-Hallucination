import json
from pathlib import Path

import pytest

from evolution.checkpoint import CycleJournal
from evolution.contracts import empty_registry


def test_resume_keeps_completed_and_unresolved_observations(tmp_path):
    calls = []
    def evaluate(cases, **kwargs):
        case = cases[0]
        calls.append(case)
        if case == "second" and calls.count(case) == 1:
            raise RuntimeError("interrupted transport")
        return [{"case": case, "prediction": None, "decision_authority": "unresolved"}]

    journal = CycleJournal(tmp_path, {"input": "unchanged"}, resume=False)
    kwargs = dict(registry=empty_registry(), fresh=False, evaluate=evaluate, kwargs={"workers": 1})
    with pytest.raises(RuntimeError, match="interrupted"):
        journal.evaluate(["first", "second"], **kwargs)
    resumed = CycleJournal(tmp_path, {"input": "unchanged"}, resume=True)
    rows = resumed.evaluate(["first", "second"], **kwargs)
    assert calls == ["first", "second", "second"]
    assert [r["prediction"] for r in rows] == [None, None]
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        CycleJournal(tmp_path, {"input": "changed"}, resume=True)
    path = next((tmp_path / "arms").rglob("*.json"))
    envelope = json.loads(path.read_text())
    envelope["row"]["prediction"] = True
    path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="integrity"):
        resumed.evaluate(["first", "second"], **kwargs)


def test_resume_refuses_missing_proposal_or_historical_checkpoint(tmp_path):
    with pytest.raises(RuntimeError, match="no checkpoint"):
        CycleJournal(tmp_path, {}, resume=True)
    journal = CycleJournal(tmp_path, {}, resume=False)
    journal.save(proposing=True)
    with pytest.raises(RuntimeError, match="proposal call interrupted"):
        CycleJournal(tmp_path, {}, resume=True)


def test_cycle_resume_keeps_proposals_and_completed_arms(tmp_path, monkeypatch):
    from evolution.tests.test_engine_smoke import _make_dataset, _config
    from evolution.backends import MockDetectorBackend
    from evolution.engine import EvolutionEngine
    from evolution.proposer import ProposalBatch
    from evolution.splits import EvolutionSplit

    cases = _make_dataset(tmp_path / "data", n=20)
    config = _config(tmp_path)
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    proposals = []
    class Proposer:
        name = "resume_test"
        def propose(self, *args, **kwargs):
            proposals.append(1)
            return ProposalBatch(candidates=[], joint_groups=[], notes=[])
    backend = MockDetectorBackend()
    engine = EvolutionEngine(config, backend=backend, split=split, mini=True, proposer=Proposer())
    validate = engine._validate_proposal_batch
    def interrupt(*args, **kwargs):
        raise RuntimeError("interrupted after proposal persisted")
    monkeypatch.setattr(engine, "_validate_proposal_batch", interrupt)
    with pytest.raises(RuntimeError, match="after proposal"):
        engine.run_cycle(1)
    completed_paths = list((tmp_path / "work" / "checkpoints").rglob("arms/*/*.json"))
    assert completed_paths
    original_observations = {str(p): p.read_bytes() for p in completed_paths}
    engine = EvolutionEngine(config, backend=MockDetectorBackend(), split=split,
                             mini=True, proposer=Proposer())
    report = engine.run_cycle(1, resume=True)
    assert report["state"] == "completed"
    assert proposals == [1]
    assert all(Path(p).read_bytes() == value for p, value in original_observations.items())
    assert engine.ledger.verify()["valid"]
