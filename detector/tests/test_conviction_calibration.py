"""Conviction authority is granted by calibration, not by the design table.

The detector had a zero-LLM path to a terminal ``verified_error``: a built-in
deterministic predicate reports a convicting verdict, a ``decision_guard`` binds
that evidence row, and ``detector.adjudication`` records an ``authoritative``
conviction without the judge, the recall net or the defense hearing being
consulted. Nothing in that path measured whether the predicate was right, and
the measurement that existed (``evolution/calibration.py``) audited the judge's
stated confidence and was not on any runtime path.

These tests pin the guard that closes the gap:

* a predicate whose record shows zero false positives on the labelled
  gold-clean corpus keeps its conviction authority;
* a predicate whose record shows any clean-set false positive, is stale against
  the predicate's own code, or is stale against the corpus loses it, and the
  guard binding that would have used it is suppressed rather than satisfied;
* a caller who hands ``adjudicate`` a mechanical evidence row *directly* cannot
  restore an authority the calibration refused -- the same filter applies there;
* a caller whose record was fabricated (counts edit without re-measuring) is
  refused by the record's own content digest;
* the typed-DSL route keeps its authority, so the fix does not quietly remove
  the conviction route the framework legitimately has.

Every test builds a real (tiny) corpus in ``tmp_path`` and measures it, so the
decision is exercised end to end rather than mocked.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from detector.adjudication import (
    AUTHORITY_AUTHORITATIVE,
    STATE_CLEAN,
    STATE_ERROR,
    adjudicate,
)
from detector.conviction_calibration import (
    DEFAULT_ARTIFACT,
    MIN_CLEAN_CASES,
    ConvictionCalibration,
    build_artifact,
    content_digest,
    dependency_items,
    source_fingerprint,
)
from detector.interventions import (
    InterventionPlan,
    build_intervention_registry,
    compile_runtime_plan,
    conviction_verdict_table,
    conviction_verdicts_for,
)
from detector.packet import build_packet


def test_cold_default_view_cannot_change_identity_after_journal_binding(tmp_path, monkeypatch):
    from copy import deepcopy
    from threading import Event, Thread
    import detector.conviction_calibration as calibration
    from evolution.checkpoint import CycleJournal

    artifact = tmp_path / "calibration.json"
    artifact.write_text(json.dumps({"synthetic": "A"}), encoding="utf-8")
    monkeypatch.setattr(calibration, "DEFAULT_ARTIFACT", artifact)
    monkeypatch.setattr(calibration, "DEFAULT_CORPUS_ROOT", tmp_path / "empty-corpus")
    monkeypatch.setattr(calibration, "_CACHE", {})
    read_a, release_a, finished_a, backend_entered = (Event() for _ in range(4))
    reads, errors = [], []
    load = calibration.ConvictionCalibration._load_artifact

    def paused_load(self):
        payload = load(self)
        reads.append(payload["synthetic"])
        if payload["synthetic"] == "A":
            read_a.set()
            assert release_a.wait(5)
        return payload

    monkeypatch.setattr(calibration.ConvictionCalibration, "_load_artifact", paused_load)
    names = ["final_delivery_present"]

    def preload():
        try:
            calibration.default_calibration().identity(names)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished_a.set()

    def release_load():
        # Release independently of backend progress so serialized loading can finish.
        backend_entered.wait(1)
        release_a.set()

    journal = CycleJournal(tmp_path / "journal", {}, resume=False)
    preloader = Thread(target=preload)
    preloader.start()
    assert read_a.wait(3)
    artifact.write_text(json.dumps({"synthetic": "B"}), encoding="utf-8")
    releaser = Thread(target=release_load)
    releaser.start()
    observations = []

    def backend(cases, **kwargs):
        bound = deepcopy(journal.state["authority"])
        backend_entered.set()
        assert finished_a.wait(3)
        used = calibration.default_calibration().identity(names)
        observations.append((bound, used))
        return [{"case": cases[0], "used_identity": used}]

    try:
        rows = journal.evaluate(["case"], registry=_mechanical_registry(), fresh=False,
                                evaluate=backend, kwargs={"workers": 1})
    finally:
        release_a.set()
        preloader.join(3)
        releaser.join(3)
    assert not preloader.is_alive() and not releaser.is_alive()
    assert errors == []
    assert observations[0][0] == rows[0]["used_identity"]
    assert reads == ["A"]


def test_concurrent_default_requests_share_one_calibration_view(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    import detector.conviction_calibration as calibration

    monkeypatch.setattr(calibration, "DEFAULT_ARTIFACT", tmp_path / "unused.json")
    monkeypatch.setattr(calibration, "DEFAULT_CORPUS_ROOT", tmp_path / "unused-corpus")
    monkeypatch.setattr(calibration, "_CACHE", {})
    constructor = calibration.ConvictionCalibration
    barrier = Barrier(8)
    created = []

    def paused_constructor():
        Event().wait(0.1)
        view = constructor()
        created.append(view)
        return view

    def request(index):
        barrier.wait(3)
        return calibration.default_calibration()

    monkeypatch.setattr(calibration, "ConvictionCalibration", paused_constructor)
    with ThreadPoolExecutor(max_workers=8) as pool:
        views = list(pool.map(request, range(8)))
    assert len(created) == 1
    assert all(view is views[0] for view in views)


def test_journal_binding_uses_one_view_for_previous_and_requested_authority(tmp_path, monkeypatch):
    import detector.conviction_calibration as calibration
    from evolution.checkpoint import CycleJournal

    a = ConvictionCalibration(artifact={"synthetic": "A"}, corpus_root=tmp_path / "empty")
    b = ConvictionCalibration(artifact={"synthetic": "B"}, corpus_root=tmp_path / "empty")
    monkeypatch.setattr(calibration, "default_calibration", lambda: a)
    journal = CycleJournal(tmp_path / "journal", {}, resume=False)
    registry = _mechanical_registry()
    journal.evaluate(["case"], registry=registry, fresh=False,
                     evaluate=lambda cases, **kwargs: [{"case": cases[0]}], kwargs={"workers": 1})
    before = {path: path.read_bytes() for path in journal.directory.rglob("*.json")}
    choices = iter([b, a, b])
    monkeypatch.setattr(calibration, "default_calibration", lambda: next(choices, b))
    with pytest.raises(RuntimeError, match="authority input changed"):
        journal.evaluate(["case"], registry=registry, fresh=True,
                         evaluate=lambda *args, **kwargs: pytest.fail("changed view cannot be measured"),
                         kwargs={"workers": 1})
    assert {path: path.read_bytes() for path in journal.directory.rglob("*.json")} == before


def test_journal_rechecks_authority_before_committing_observation(tmp_path, monkeypatch):
    import detector.conviction_calibration as calibration
    from evolution.checkpoint import CycleJournal

    a = ConvictionCalibration(artifact={"synthetic": "A"}, corpus_root=tmp_path / "empty")
    b = ConvictionCalibration(artifact={"synthetic": "B"}, corpus_root=tmp_path / "empty")
    current = [a]
    monkeypatch.setattr(calibration, "default_calibration", lambda: current[0])
    journal = CycleJournal(tmp_path / "journal", {}, resume=False)
    checkpoint = []

    def backend(cases, **kwargs):
        checkpoint.append(journal.path.read_bytes())
        current[0] = b
        return [{"case": cases[0], "used_identity": b.identity(["final_delivery_present"])}]

    with pytest.raises(RuntimeError, match="authority input changed"):
        journal.evaluate(["case"], registry=_mechanical_registry(), fresh=False,
                         evaluate=backend, kwargs={"workers": 1})
    assert not list((journal.directory / "arms").rglob("*.json"))
    assert journal.path.read_bytes() == checkpoint[0]


PREDICATE = "final_delivery_present"


# ---------------------------------------------------------------------------
# A real miniature corpus
# ---------------------------------------------------------------------------

def _clean_record(question: str = "What is the capital of Peru?") -> dict:
    """A gold-clean trajectory: an observation carries the answer."""
    return {
        "question": question,
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "search", "arguments": {"q": question}}],
                "tool_responses": [
                    "Observed output of cmd `search` executed: Lima is the "
                    "capital of Peru and the seat of its government."
                ],
            },
            {
                "step": 2,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "terminate", "arguments": {"status": "success"}}],
                "tool_responses": [
                    "Observed output of cmd `terminate` executed:\n"
                    "The interaction has been completed with status: success"
                ],
            },
        ],
        "is_hallucination": "false",
    }


def _bare_record(question: str = "What is the capital of Peru?") -> dict:
    """A trajectory that terminates successfully with no delivery at all."""
    return {
        "question": question,
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "terminate", "arguments": {"status": "success"}}],
                "tool_responses": [
                    "Observed output of cmd `terminate` executed:\n"
                    "The interaction has been completed with status: success"
                ],
            },
        ],
        "is_hallucination": "true",
    }


def _corpus(root: pathlib.Path, *, clean: int, error: int, extra_clean=None) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(clean):
        record = _clean_record(f"Clean question {index}?")
        (root / f"clean_{index:04d}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8",
        )
    for index in range(error):
        record = _bare_record(f"Bare question {index}?")
        (root / f"error_{index:04d}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8",
        )
    for name, record in (extra_clean or {}).items():
        (root / name).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8",
        )
    return root


def _sealed(artifact: dict) -> dict:
    """Re-seal an artifact after a deliberate edit, so the test targets one
    check at a time instead of always tripping the content digest first."""
    resealed = {key: value for key, value in artifact.items()
                if key != "content_digest"}
    resealed["content_digest"] = content_digest(resealed)
    return resealed


@pytest.fixture
def big_corpus(tmp_path):
    """Enough clean cases to satisfy ``MIN_CLEAN_CASES``."""
    return _corpus(
        tmp_path / "corpus", clean=MIN_CLEAN_CASES + 2, error=4,
    )


@pytest.fixture
def good_record(big_corpus):
    return build_artifact(big_corpus)


# ---------------------------------------------------------------------------
# The record is a measurement, and it is checked
# ---------------------------------------------------------------------------

def test_record_measures_zero_false_positives_and_keeps_authority(big_corpus, good_record):
    entry = good_record["predicates"][PREDICATE]
    assert entry["clean_cases"] == MIN_CLEAN_CASES + 2
    assert entry["clean_violations"] == 0
    assert entry["error_hits"] == 4
    assert entry["admitted"] is True
    view = ConvictionCalibration(artifact=good_record, corpus_root=big_corpus)
    assert view.admits(PREDICATE) == (True, "")


def test_cache_binds_the_calibration_actually_used(big_corpus, good_record, tmp_path, monkeypatch):
    from detector import conviction_calibration as calibration
    from detector.dataset import project_record
    from evolution.backends import CachingBackend

    class Backend:
        name = "calibrated_test"
        calls = 0

        def run_case(self, record, *, case_id, registry=None, gold=None):
            self.calls += 1
            return {"case_id": case_id, "decision_authority": "authoritative",
                    "calibration_admitted": calibration.default_calibration().admits(PREDICATE)[0]}

    backend = Backend()
    cache = CachingBackend(backend, tmp_path / "cache")
    record = project_record(_clean_record())
    registry = _mechanical_registry()
    first_view = ConvictionCalibration(artifact=good_record, corpus_root=big_corpus)
    monkeypatch.setattr(calibration, "default_calibration", lambda: first_view)
    assert cache.run_case(record, case_id="case", registry=registry)["calibration_admitted"] is True
    assert cache.run_case(record, case_id="case", registry=registry)["calibration_admitted"] is True
    changed_record = _sealed({**good_record, "generated_at_utc": "different synthetic record"})
    second_view = ConvictionCalibration(artifact=changed_record, corpus_root=big_corpus)
    monkeypatch.setattr(calibration, "default_calibration", lambda: second_view)
    cache.run_case(record, case_id="case", registry=registry)
    assert backend.calls == 2
    (big_corpus / "clean_0000.json").write_text(json.dumps(_clean_record("changed task")), encoding="utf-8")
    third_view = ConvictionCalibration(artifact=changed_record, corpus_root=big_corpus)
    monkeypatch.setattr(calibration, "default_calibration", lambda: third_view)
    assert cache.run_case(record, case_id="case", registry=registry)["calibration_admitted"] is False
    assert backend.calls == 3


def test_calibration_identity_is_the_immutable_view_used_by_admission(big_corpus, good_record):
    view = ConvictionCalibration(artifact=good_record, corpus_root=big_corpus)
    before = view.identity([PREDICATE])
    returned_status = view.status()
    returned_status[PREDICATE]["admitted"] = False
    good_record["generated_at_utc"] = "caller mutation after loading"
    assert view.admits(PREDICATE) == (True, "")
    assert view.identity([PREDICATE]) == before


def test_unused_calibration_does_not_participate_in_execution_identity(monkeypatch):
    from detector.interventions import authority_identity
    def unexpected_read():
        raise AssertionError("unused calibration was read")
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", unexpected_read)
    assert authority_identity(build_intervention_registry([])) is None


def test_journal_refuses_calibration_drift_without_redrawing(big_corpus, good_record, tmp_path, monkeypatch):
    from evolution.checkpoint import CycleJournal
    registry = _mechanical_registry()
    first_view = ConvictionCalibration(artifact=good_record, corpus_root=big_corpus)
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda: first_view)
    journal = CycleJournal(tmp_path / "journal", {}, resume=False)
    calls = []
    def evaluate(cases, **kwargs):
        calls.append(cases[0])
        return [{"case": cases[0], "prediction": False}]
    args = {"registry": registry, "fresh": False, "evaluate": evaluate, "kwargs": {"workers": 1}}
    journal.evaluate(["case"], **args)
    second_view = ConvictionCalibration(
        artifact=_sealed({**good_record, "generated_at_utc": "another synthetic artifact"}),
        corpus_root=big_corpus,
    )
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda: second_view)
    with pytest.raises(RuntimeError, match="authority input changed"):
        journal.evaluate(["case"], **args)
    assert calls == ["case"]


def test_journal_freezes_authority_across_new_candidate_arms(big_corpus, good_record, tmp_path, monkeypatch):
    from evolution.checkpoint import CycleJournal
    first_view = ConvictionCalibration(artifact=good_record, corpus_root=big_corpus)
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda: first_view)
    journal = CycleJournal(tmp_path / "journal", {}, resume=False)
    calls = []

    def evaluate(cases, **kwargs):
        calls.append(cases[0])
        return [{"case": cases[0], "prediction": False}]

    registry = _mechanical_registry()
    args = {"fresh": False, "evaluate": evaluate, "kwargs": {"workers": 1}}
    journal.evaluate(["case"], registry=registry, **args)
    before = journal.path.read_bytes()
    changed = _sealed({**good_record, "generated_at_utc": "changed between candidate arms"})
    second_view = ConvictionCalibration(artifact=changed, corpus_root=big_corpus)
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda: second_view)
    entries = [dict(entry) for entry in registry["interventions"]]
    entries[0]["intervention_id"] += "-another"
    for entry in entries:
        entry.pop("content_hash", None)
    with pytest.raises(RuntimeError, match="authority input changed"):
        journal.evaluate(["case"], registry=build_intervention_registry(entries), **args)
    assert calls == ["case"]
    assert journal.path.read_bytes() == before


def test_completed_cycle_resume_refuses_consumed_authority_drift(big_corpus, good_record, tmp_path, monkeypatch):
    from evolution.backends import MockDetectorBackend
    from evolution.engine import EvolutionEngine
    from evolution.proposer import ProposalBatch
    from evolution.splits import EvolutionSplit
    from evolution.tests.test_measurement_integrity import _config, _make_dataset

    first_view = ConvictionCalibration(artifact=good_record, corpus_root=big_corpus)
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda: first_view)
    cases = _make_dataset(tmp_path / "data")
    config = _config(tmp_path)
    config["audit"]["enabled"] = False
    split = EvolutionSplit(name="synthetic", pool=cases, sentinel=cases[:4],
                           gain=cases[4:12], patrol_pool=cases[12:], mini=True)
    with EvolutionEngine(config, backend=MockDetectorBackend(), split=split, mini=True) as engine:
        engine.registry.write(_mechanical_registry()["interventions"], cycle=0,
                              reason="synthetic authority", library_cfg=config["library"])
        report = engine.run_cycle(1, proposal_batch=ProposalBatch(candidates=[], joint_groups=[], notes=[]))
        assert report["state"] == "completed"
        assert engine.run_cycle(1, resume=True) == report
        path = tmp_path / "work/checkpoints/cycle001/checkpoint.json"
        before = path.read_bytes()
        second_view = ConvictionCalibration(
            artifact=_sealed({**good_record, "generated_at_utc": "changed after cycle"}),
            corpus_root=big_corpus,
        )
        monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda: second_view)
        with pytest.raises(RuntimeError, match="authority input changed"):
            engine.run_cycle(1, resume=True)
        assert path.read_bytes() == before


def test_record_is_bound_to_the_predicate_source(tmp_path, big_corpus):
    """A record whose source fingerprint no longer matches is stale.

    The fingerprint is derived from the predicate's own code and the data it
    reads, so this pins that something the predicate depends on moved -- not
    merely that some file in the package was touched.
    """
    artifact = build_artifact(big_corpus)
    stale = _sealed({
        **artifact,
        "predicates": {
            **artifact["predicates"],
            PREDICATE: {
                **artifact["predicates"][PREDICATE],
                "source_fingerprint": "0" * 64,
            },
        },
    })
    admitted, reason = ConvictionCalibration(
        artifact=stale, corpus_root=big_corpus,
    ).admits(PREDICATE)
    assert (admitted, reason) == (False, "source_fingerprint_mismatch")


def test_source_fingerprint_follows_reachable_code_not_the_whole_module(monkeypatch):
    """The fingerprint is derived, so it is neither too narrow nor too broad.

    Too broad (a whole-file digest) would invalidate a predicate's record when
    an unrelated predicate's data is edited, and a reader who learns to
    regenerate the record blindly stops reading it. Too narrow would miss a
    dependency. So: a value the predicate reads must move its fingerprint, and a
    value only another predicate reads must not.
    """
    from detector import predicates as predicates_module

    delivery_before = source_fingerprint(PREDICATE)
    movement_before = source_fingerprint("required_action_present")

    # ``_movement_requirements`` reads this pattern; the delivery predicate
    # does not.
    monkeypatch.setattr(
        predicates_module, "_DESTINATION_RE",
        predicates_module.re.compile(r"\bnowhere\b", predicates_module.re.I),
    )
    assert source_fingerprint("required_action_present") != movement_before, (
        "a pattern the movement gate reads did not move its fingerprint"
    )
    assert source_fingerprint(PREDICATE) == delivery_before, (
        "the delivery predicate's fingerprint moved when a pattern it never "
        "reads was edited"
    )
    monkeypatch.undo()


def test_source_fingerprint_follows_a_constant_the_predicate_reads(monkeypatch):
    from detector import predicates as predicates_module

    before = source_fingerprint(PREDICATE)
    monkeypatch.setattr(
        predicates_module, "_MIN_DELIVERY_CHARS",
        predicates_module._MIN_DELIVERY_CHARS + 1,
    )
    assert source_fingerprint(PREDICATE) != before, (
        "the fingerprint did not move when a constant the predicate reads did"
    )
    monkeypatch.undo()


def test_source_fingerprint_follows_a_helper_in_another_module():
    """A predicate that delegates to ``detector.packet`` depends on it.

    ``final_delivery_present`` reads ``delivered_result`` from the packet
    module, so the packet module's own answer-anchor vocabulary is part of what
    the record must pin. A fingerprint that stopped at the predicate's own file
    would let a change there invalidate the verdict without invalidating the
    record.
    """
    items = dependency_items(PREDICATE)
    assert any(
        key.startswith("detector.packet.") for key in items
    ), f"no packet-module dependency recorded: {sorted(items)}"
    assert "tuple:_ANSWER_TOOL_HINTS" in items


def test_record_is_bound_to_the_corpus(tmp_path, big_corpus):
    """A corpus edit after the measurement refuses the record, by file digest."""
    artifact = build_artifact(big_corpus)
    (big_corpus / "clean_0999.json").write_text(
        json.dumps(_clean_record("A question added after the measurement?"),
                   ensure_ascii=False),
        encoding="utf-8",
    )
    admitted, reason = ConvictionCalibration(
        artifact=artifact, corpus_root=big_corpus,
    ).admits(PREDICATE)
    assert admitted is False
    assert reason in {"corpus_fingerprint_mismatch", "clean_fingerprint_mismatch"}


def test_fabricated_counts_are_refused_by_the_content_digest(big_corpus):
    """Editing a count without re-measuring does not buy authority.

    Two edits, neither of which trips another check on its own: a bumped
    ``error_hits`` (no code path compares it) and a ``clean_violations`` reset
    to zero on a record that *measured* one. The record's own digest is what
    refuses them, which is why the digest is not decoration.
    """
    artifact = build_artifact(big_corpus)
    for edited in (
        {"error_hits": 999},
        {"clean_cases": 999},
        {"unbuildable_cases": 3},
    ):
        forged = {
            **artifact,
            "predicates": {
                **artifact["predicates"],
                PREDICATE: {
                    **artifact["predicates"][PREDICATE],
                    **edited,
                },
            },
        }
        admitted, reason = ConvictionCalibration(
            artifact=forged, corpus_root=big_corpus,
        ).admits(PREDICATE)
        assert (admitted, reason) == (False, "content_digest_mismatch"), edited


def test_a_measured_false_positive_cannot_be_edited_away(tmp_path):
    """The forged-record case that matters: erasing the one clean FP.

    Built on a corpus whose record measured exactly one clean-set false
    positive, so the edit changes the *decision*, not just a number.
    """
    forked = _corpus(tmp_path / "forked_forgery", clean=MIN_CLEAN_CASES, error=1)
    (forked / "clean_9999.json").write_text(
        json.dumps({**_bare_record(), "is_hallucination": "false"},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    artifact = build_artifact(forked)
    assert artifact["predicates"][PREDICATE]["clean_violations"] == 1
    assert artifact["predicates"][PREDICATE]["admitted"] is False

    forged = {
        **artifact,
        "predicates": {
            **artifact["predicates"],
            PREDICATE: {
                **artifact["predicates"][PREDICATE],
                "clean_violations": 0,
                "clean_violation_cases": [],
                "admitted": True,
            },
        },
    }
    admitted, reason = ConvictionCalibration(
        artifact=forged, corpus_root=forked,
    ).admits(PREDICATE)
    assert (admitted, reason) == (False, "content_digest_mismatch")


def test_record_with_a_measured_false_positive_is_refused(big_corpus, tmp_path):
    """The whole point: one clean-set false positive removes the authority."""
    forked = _corpus(tmp_path / "forked", clean=MIN_CLEAN_CASES, error=1)
    # A "clean" case that is structurally a bare terminal success: the
    # predicate calls it a violation, and the record must say so.
    (forked / "clean_9999.json").write_text(
        json.dumps({**_bare_record(), "is_hallucination": "false"},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    artifact = build_artifact(forked)
    entry = artifact["predicates"][PREDICATE]
    assert entry["clean_violations"] == 1
    assert entry["clean_violation_cases"] == ["clean_9999.json"]
    assert entry["admitted"] is False
    admitted, reason = ConvictionCalibration(
        artifact=artifact, corpus_root=forked,
    ).admits(PREDICATE)
    assert (admitted, reason) == (False, "clean_set_false_positive")


def test_record_measured_on_too_few_clean_cases_is_refused(tmp_path):
    small = _corpus(tmp_path / "small", clean=3, error=1)
    artifact = build_artifact(small)
    assert artifact["predicates"][PREDICATE]["admitted"] is False
    admitted, reason = ConvictionCalibration(
        artifact=artifact, corpus_root=small,
    ).admits(PREDICATE)
    assert (admitted, reason) == (False, "insufficient_clean_cases")


def test_missing_record_refuses_every_builtin_predicate(tmp_path):
    view = ConvictionCalibration(
        artifact=tmp_path / "absent.json", corpus_root=tmp_path,
    )
    for predicate in ("required_action_present", "verbatim_fidelity",
                      "final_delivery_present"):
        admitted, reason = view.admits(predicate)
        assert admitted is False
        assert reason == "artifact_unavailable"


def test_absent_corpus_is_reported_not_silently_unverified(big_corpus):
    """A checkout without the (gitignored) corpus still runs, and says so.

    The record's own provenance checks -- schema, digest, source fingerprint,
    vocabulary -- still apply; only the live re-measurement is unavailable, and
    that is a warning rather than a refusal.
    """
    artifact = build_artifact(big_corpus)
    view = ConvictionCalibration(
        artifact=artifact, corpus_root=big_corpus / "does-not-exist",
    )
    assert view.admits(PREDICATE) == (True, "")
    assert view.warnings() == ["corpus_unavailable"]
    assert view.summary()["unavailable"] == ["corpus_unavailable"]


def test_recorded_vocabulary_mismatch_refuses(big_corpus):
    """A record measured against another conviction vocabulary is not evidence."""
    artifact = build_artifact(big_corpus)
    changed = _sealed({
        **artifact,
        "predicates": {
            **artifact["predicates"],
            PREDICATE: {
                **artifact["predicates"][PREDICATE],
                "convicting_verdicts": ["present"],
            },
        },
    })
    admitted, reason = ConvictionCalibration(
        artifact=changed, corpus_root=big_corpus,
    ).admits(PREDICATE)
    assert (admitted, reason) == (False, "conviction_verdicts_mismatch")


# ---------------------------------------------------------------------------
# The guard binding and the terminal decision
# ---------------------------------------------------------------------------

def _mechanical_registry():
    """An evidence check plus the guard that convicts on it."""
    return build_intervention_registry([
        {
            "schema_version": "detector.intervention.v1",
            "intervention_id": "IV-E",
            "type": "evidence_check",
            "hook": "judge.post",
            "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
            "body": {
                "predicate": PREDICATE,
                "runner": "deterministic",
                "output_verdicts": ["present", "missing", "not_applicable"],
            },
            "claim": {"expected_behavior": "append a delivery fact",
                      "target_polarity": "boundary"},
            "provenance": {"cycle": 1, "failure_family": "FAM-delivery",
                           "failure_cases": ["A/001.json", "A/002.json"]},
        },
        {
            "schema_version": "detector.intervention.v1",
            "intervention_id": "IV-G",
            "type": "decision_guard",
            "hook": "adjudicate.guard",
            "trigger": {
                "packet_features": ["has_tool_calls"],
                "facts": [
                    f"{PREDICATE}=missing",
                    "judge_positive=false",
                    "positive_source=none",
                ],
            },
            "body": {
                "action": "convict_mechanical_violation",
                "evidence_predicate": PREDICATE,
                "action_not_when": "Never when a delivery anchor is present.",
                "action_not_when_facts": [f"{PREDICATE}=present"],
            },
            "claim": {"expected_behavior": "convict a bare terminal success",
                      "target_polarity": "recall"},
            "provenance": {"cycle": 1, "failure_family": "FAM-delivery",
                           "failure_cases": ["A/001.json", "A/002.json"]},
        },
    ])


def _bare_packet():
    return build_packet({
        "question": "What is the capital of Peru?",
        "history": [
            {
                "step": 1,
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "terminate",
                                "arguments": {"status": "success"}}],
                "tool_responses": [
                    "Observed output of cmd `terminate` executed:\n"
                    "The interaction has been completed with status: success"
                ],
            },
        ],
    })


def test_calibrated_predicate_can_still_convict(big_corpus, monkeypatch):
    """The guard route keeps working for a predicate the record admits."""
    artifact = build_artifact(big_corpus)
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda:
                        ConvictionCalibration(artifact=artifact, corpus_root=big_corpus))
    plan = compile_runtime_plan(
        _mechanical_registry(),
        conviction_calibration=ConvictionCalibration(
            artifact=artifact, corpus_root=big_corpus,
        ),
    )
    rows = plan.evidence_for("judge.post", _bare_packet(), {})
    assert [row["verdict"] for row in rows] == ["missing"]
    actions = plan.guard_actions(_bare_packet(), fact_rows=[{
        f"{PREDICATE}": "missing",
        "judge_positive": "false",
        "positive_source": "none",
    }])
    assert actions == ["convict_mechanical_violation"]
    assert plan.mechanical_guard_evidence[0]["predicate"] == PREDICATE


def test_uncalibrated_predicate_cannot_convict_and_says_why(tmp_path, big_corpus):
    """The same registry, the same evidence, a record that refuses."""
    artifact = build_artifact(big_corpus)
    refused = _sealed({
        **artifact,
        "predicates": {
            **artifact["predicates"],
            PREDICATE: {
                **artifact["predicates"][PREDICATE],
                "clean_cases": 0,
            },
        },
    })
    plan = compile_runtime_plan(
        _mechanical_registry(),
        conviction_calibration=ConvictionCalibration(
            artifact=refused, corpus_root=big_corpus,
        ),
    )
    packet = _bare_packet()
    rows = plan.evidence_for("judge.post", packet, {})
    # The row is still evidence: refusal removes authority, not observability.
    assert [row["verdict"] for row in rows] == ["missing"]
    actions = plan.guard_actions(packet, fact_rows=[{
        f"{PREDICATE}": "missing",
        "judge_positive": "false",
        "positive_source": "none",
    }])
    assert actions == []
    assert plan.mechanical_guard_evidence == []
    assert plan.uncalibrated_convictions[PREDICATE] == "insufficient_clean_cases"
    traces = [row for row in plan.fired if row["event"] == "guard_suppressed"]
    assert any("not conviction-calibrated" in row["detail"] for row in traces)
    summary = plan.deployment_summary()
    assert summary["uncalibrated_convictions"][PREDICATE] == (
        "insufficient_clean_cases"
    )


def test_adjudicate_refuses_a_directly_supplied_row_for_an_uncalibrated_predicate(
    big_corpus, monkeypatch,
):
    """The second gate holds on its own.

    A caller that skipped the plan and handed ``adjudicate`` a fully-formed
    mechanical evidence row must not be able to convict with a predicate the
    calibration refused -- otherwise the guard would be the only thing standing
    between an unmeasured parser and a terminal ``authoritative`` error.
    """
    artifact = build_artifact(big_corpus)
    monkeypatch.setattr("detector.conviction_calibration.default_calibration", lambda:
                        ConvictionCalibration(artifact=artifact, corpus_root=big_corpus))
    refused = _sealed({
        **artifact,
        "predicates": {
            **artifact["predicates"],
            PREDICATE: {
                **artifact["predicates"][PREDICATE],
                "clean_violations": 1,
            },
        },
    })
    table = conviction_verdict_table(
        calibration=ConvictionCalibration(artifact=refused, corpus_root=big_corpus),
    )
    packet = _bare_packet()
    row = {
        "action": "convict_mechanical_violation",
        "intervention_id": "IV-G",
        "evidence_intervention_id": "IV-E",
        "failure_family": "FAM-delivery",
        "predicate": PREDICATE,
        "runner": "deterministic",
        "verdict": "missing",
    }
    common = dict(
        packet=packet,
        judge_result={"ok": True, "parsed": {
            "is_hallucination": False, "confidence": "high",
            "delivered_result": "", "correct_result": "", "contracts": [],
        }},
        judge_status={"state": "ok"},
        recall_result={"ok": True, "candidates": []},
        recall_status={"state": "ok"},
        upgrade_result={"ok": True, "upgraded_candidate": None,
                        "upgraded_candidates": []},
        upgrade_status={"state": "ok"},
        tracer_result=None, tracer_status={"state": "not_run"},
        defense_result=None, defense_status={"state": "not_run"},
        guard_actions=["convict_mechanical_violation"],
        mechanical_evidence=[row],
        hearings=[], hearing_facts=[],
    )
    proof = adjudicate(**common, predicate_conviction_verdicts=table)
    assert proof["state"] == STATE_CLEAN

    # And with an admitting record the same row does convict, so the
    # assertion above is about the calibration and not about the row's shape.
    admitting = conviction_verdict_table(
        calibration=ConvictionCalibration(
            artifact=build_artifact(big_corpus), corpus_root=big_corpus,
        ),
    )
    proof = adjudicate(**common, predicate_conviction_verdicts=admitting)
    assert proof["state"] == STATE_ERROR
    assert proof["decision_authority"] == AUTHORITY_AUTHORITATIVE
    assert proof["terminal_basis"]["decisive_facts"]["positive_source"] == (
        "mechanical_violation"
    )


def test_the_legacy_whole_table_cannot_bypass_the_calibration(big_corpus):
    """A caller who passes ``PREDICATE_CONVICTION_VERDICTS`` gets no exemption.

    ``conviction_verdicts_for`` accepts a predicate-only table for legacy
    callers, so the calibration filter has to be applied *there* as well --
    otherwise the refusal would be undone by handing over the very table the
    design uses.
    """
    from detector.predicates import PREDICATE_CONVICTION_VERDICTS

    refused = _sealed({
        **build_artifact(big_corpus),
        "predicates": {},
    })
    allowed = conviction_verdicts_for(
        PREDICATE_CONVICTION_VERDICTS, PREDICATE, "deterministic",
        calibration=ConvictionCalibration(
            artifact=refused, corpus_root=big_corpus,
        ),
    )
    assert allowed == frozenset()


def test_typed_dsl_conviction_route_is_unchanged(big_corpus):
    """Calibration governs the built-in parsers; a typed-DSL program keeps its
    authority, so the framework's legitimate conviction route is not removed."""
    program = {
        "schema_version": "detector.predicate_program.v1",
        "name": "evo_program_route",
        "description": "a program whose rule fires on the probe packet",
        "verdicts": ["contradicted", "not_applicable"],
        "conviction_verdicts": ["contradicted"],
        "default_verdict": "not_applicable",
        "rules": [{
            "field": "tool_names",
            "operator": "any_contains",
            "value": "terminate",
            "verdict": "contradicted",
            "detail": "the terminate call is present",
        }],
    }
    registry = build_intervention_registry([
        {
            "schema_version": "detector.intervention.v1",
            "intervention_id": "IV-DSL-E",
            "type": "evidence_check",
            "hook": "judge.post",
            "trigger": {"packet_features": ["has_tool_calls"], "facts": []},
            "body": {
                "predicate": program["name"],
                "runner": "typed_dsl",
                "predicate_definition": program,
                "output_verdicts": ["contradicted", "not_applicable"],
            },
            "claim": {"expected_behavior": "observe the program",
                      "target_polarity": "boundary"},
            "provenance": {"cycle": 1, "failure_family": "FAM-dsl",
                           "failure_cases": ["A/001.json", "A/002.json"]},
        },
        {
            "schema_version": "detector.intervention.v1",
            "intervention_id": "IV-DSL-G",
            "type": "decision_guard",
            "hook": "adjudicate.guard",
            "trigger": {
                "packet_features": ["has_tool_calls"],
                "facts": [
                    f"{program['name']}=contradicted",
                    "judge_positive=false",
                    "positive_source=none",
                ],
            },
            "body": {
                "action": "convict_mechanical_violation",
                "evidence_predicate": program["name"],
                "action_not_when": "Never when the program observes nothing.",
                "action_not_when_facts": [f"{program['name']}=not_applicable"],
            },
            "claim": {"expected_behavior": "convict on the program",
                      "target_polarity": "recall"},
            "provenance": {"cycle": 1, "failure_family": "FAM-dsl",
                           "failure_cases": ["A/001.json", "A/002.json"]},
        },
    ])
    # A record that refuses every built-in predicate, so the only way this can
    # convict is through the typed-DSL entry.
    refused = _sealed({**build_artifact(big_corpus), "predicates": {}})
    plan = InterventionPlan.from_registry(
        registry,
        conviction_calibration=ConvictionCalibration(
            artifact=refused, corpus_root=big_corpus,
        ),
    )
    packet = _bare_packet()
    rows = plan.evidence_for("judge.post", packet, {})
    assert [row["verdict"] for row in rows] == ["contradicted"]
    table = plan.predicate_conviction_verdicts
    assert conviction_verdicts_for(table, program["name"], "typed_dsl") == (
        frozenset({"contradicted"})
    )


def test_shipped_record_covers_every_convicting_predicate():
    """The record in the tree answers for every predicate the design convicts on.

    A predicate added to ``PREDICATE_CONVICTION_VERDICTS`` without a record
    entry is refused at run time (fail-closed), so this test is the loud
    reminder to re-measure -- not a gate that grants the missing authority.
    """
    from detector.predicates import PREDICATE_CONVICTION_VERDICTS

    if not DEFAULT_ARTIFACT.is_file():
        pytest.skip("no shipped record in this checkout")
    record = json.loads(DEFAULT_ARTIFACT.read_text(encoding="utf-8"))
    missing = sorted(
        set(PREDICATE_CONVICTION_VERDICTS) - set(record.get("predicates") or {})
    )
    assert missing == [], (
        "convicting predicates with no calibration entry: "
        f"{missing}; re-run scripts/calibrate_conviction_predicates.py"
    )
    for name, entry in record["predicates"].items():
        assert entry["source_fingerprint"] == source_fingerprint(name), (
            f"{name}: the shipped record is stale against the predicate's code"
        )
