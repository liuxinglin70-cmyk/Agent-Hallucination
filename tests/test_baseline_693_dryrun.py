"""Offline dry run of the 693-case baseline over the real corpus.

Two things are proved here and they are different things.

**Zero provider calls.** ``evolution/tests/conftest.py`` guards the *test suite*
against live HTTP, and that guard is worth nothing to ``python
scripts/run_baseline_693.py``: the repository has already produced two
incidents where a probe script reached the real service. So this test does not
rely on the conftest -- it patches the transport entry points itself and, more
importantly, it exercises the injection point the script documents
(``run_baseline(detector_factory=...)`` -> ``detector.runner.Detector``), which
means the batch is served by a fake that could not call a provider even if the
transport were reachable.

**The full-corpus path works end to end.** The real 693-case list is read from
the exposure manifest, cross-checked against the directory walk, preflighted
against the record contract, run through the real ``run_batch`` (concurrency,
per-case files, manifest fingerprint, resume-by-existing-result), and scored
through the real ``evaluate_run``. What the fake replaces is the model service
and nothing else: the numbers the report produces are about the accounting, not
about detection quality, and the report itself says so.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

from detector.result import RESULT_SCHEMA
from detector.runner import safe_case_id

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_baseline_693 as baseline  # noqa: E402

DATASET_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
MANIFEST = ROOT / "datasets" / "protocols" / "development_exposure.json"
SOURCES = (
    "BFCL", "Camel", "Magentic_One", "Octotools",
    "OpenDeepSearch", "OpenManus", "SmolAgents",
)


class _OfflineDetector:
    """A deterministic stand-in whose verdicts are read off the case file.

    The rule is deliberately convoluted: it has to produce all four confusion
    cells *and* undecided rows, because a dry run that only ever produced
    ``verified_clean`` would not exercise the coverage accounting it is
    supposed to prove. Reading the dataset file is legitimate here -- this is
    the instrument being tested, not the thing under measurement.
    """

    instances: list["_OfflineDetector"] = []
    #: "ok" decides normally; "outage" leaves every third case undecided.
    mode = "ok"

    def __init__(self, config):
        self.config = config
        self.dataset_root = Path(config["dataset_root"])
        self.calls: list[str] = []
        # What this fake actually did, counted at the moment it did it. The
        # report's coverage figures are asserted against this tally rather than
        # against constants, so the test measures the accounting and not the
        # accident of which case numbers happen to exist.
        self.tally = {"authoritative": 0, "degraded": 0, "unresolved": 0}
        _OfflineDetector.instances.append(self)

    def _gold(self, case_id):
        raw = json.loads(
            (self.dataset_root / case_id).read_text(encoding="utf-8-sig")
        )
        label = raw.get("is_hallucination")
        step = raw.get("hallucination_step")
        return (
            str(label).strip().lower() == "true",
            int(step) if str(step or "").strip().isdigit() else None,
        )

    def detect(self, record, *, case_id, interventions=None):
        self.calls.append(case_id)
        gold, gold_step = self._gold(case_id)
        number = int(str(case_id).split("/")[-1].split(".")[0])
        if self.mode == "outage" and number % 3 == 0:
            self.tally["unresolved"] += 1
            return _envelope(
                case_id, state="unresolved", authority=None,
                reason={
                    "error_type": "case_deadline_exceeded",
                    "message": "dry-run outage",
                },
            )
        if number % 10 == 7:
            # A judge-only read: a terminal decision the main line must not
            # count, and a degraded row the report must show.
            self.tally["degraded"] += 1
            return _envelope(
                case_id, state="verified_clean", authority="degraded_judge_only",
            )
        self.tally["authoritative"] += 1
        if number % 2 == 0:
            return _envelope(
                case_id, state="verified_error", authority="authoritative",
                step=gold_step if number % 4 == 0 else (gold_step or 1) + 1,
            )
        return _envelope(
            case_id, state="verified_clean", authority="authoritative",
        )


def _envelope(case_id, *, state, authority, step=None, reason=None):
    return {
        "schema_version": RESULT_SCHEMA,
        "case_id": case_id,
        "state": state,
        "decision_authority": authority,
        "localization_authority": (
            authority if state == "verified_error" else None
        ),
        "runtime_valid": True,
        "responsible_event_id": None,
        "responsible_step": step,
        "incident_occurred": state == "verified_error",
        "occurrence_state": state,
        "proof": {},
        "stage_status": {},
        "providers": {},
        "authority_failure_reasons": [reason] if reason else [],
        "evidence_health": {},
        "provenance": {},
    }


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    """Refuse any HTTP exchange *from this test*, conftest or not.

    The conftest guard lives in ``evolution/tests`` and does not protect a
    script; this one is local to the dry run so the claim "zero provider calls"
    is a property of this file's execution rather than of somebody else's
    fixture.
    """
    import detector.provider as provider

    calls: list[tuple] = []

    def _refuse(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("the dry run attempted a live provider call")

    monkeypatch.setattr(provider, "_isolated_transport", _refuse, raising=False)
    monkeypatch.setattr(provider, "provider_call", _refuse, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", _refuse, raising=False)
    yield calls
    assert calls == []


def _run(tmp_path, *, mode="ok", **overrides):
    _OfflineDetector.instances.clear()
    _OfflineDetector.mode = mode
    kwargs = dict(
        dataset_root=DATASET_ROOT,
        manifest_path=MANIFEST,
        run_id=overrides.pop("run_id", "dry"),
        runs_root=tmp_path / "runs",
        report_path=tmp_path / "report.json",
        workers=8,
        config={"dry_run": True, "dataset_root": str(DATASET_ROOT)},
        detector_factory=_OfflineDetector,
        slowest=5,
    )
    kwargs.update(overrides)
    return baseline.run_baseline(**kwargs)


def test_case_list_reconciles_with_the_corpus():
    """The manifest and the directory walk must name the same 693 cases."""
    case_list = baseline.load_case_list(
        manifest_path=MANIFEST, dataset_root=DATASET_ROOT,
    )
    assert case_list["manifest_cases"] == 693
    assert case_list["disk_cases"] == 693
    assert case_list["missing_on_disk"] == []
    assert case_list["not_in_manifest"] == []
    assert case_list["duplicates"] == []
    assert case_list["path_collisions"] == {}
    assert case_list["manifest_unique"] == 693
    assert case_list["order_identical"] is True
    assert sorted(case_list["source_counts"]) == sorted(SOURCES)
    assert sum(case_list["source_counts"].values()) == 693


def test_preflight_accepts_every_case():
    case_list = baseline.load_case_list(
        manifest_path=MANIFEST, dataset_root=DATASET_ROOT,
    )
    preflight = baseline.preflight_cases(
        dataset_root=DATASET_ROOT, cases=case_list["cases"],
    )
    assert preflight["checked"] == 693
    assert preflight["unreadable"] == []
    assert preflight["invalid_records"] == []
    assert preflight["events"]["min"] >= 1


def test_dry_run_scores_the_full_list_without_a_provider(tmp_path):
    report = _run(tmp_path)
    detector = _OfflineDetector.instances[-1]
    tally = detector.tally
    coverage = report["coverage"]
    assert coverage["requested_cases"] == 693
    assert coverage["found_results"] == 693
    assert coverage["missing_cases"] == []
    # The report must agree with what the instrument actually did, row for row.
    assert coverage["authoritative"] == tally["authoritative"]
    assert coverage["degraded"] == tally["degraded"]
    assert coverage["provider_unresolved"] == tally["unresolved"]
    assert coverage["no_terminal_decision"] == tally["unresolved"]
    assert tally["degraded"] > 0 and tally["authoritative"] > 0, (
        "the dry run must exercise both a counted and an uncounted authority"
    )
    # The fake decides every even case an error and every odd case clean, and
    # the corpus is 443 error / 250 clean, so all four cells are populated.
    metrics = report["metrics"]
    assert metrics["tp"] + metrics["fp"] + metrics["fn"] + metrics["tn"] > 0
    assert metrics["tp"] > 0 and metrics["fp"] > 0
    assert metrics["fn"] > 0 and metrics["tn"] > 0
    assert metrics["decisive"] == tally["authoritative"]
    assert metrics["decisive"] + metrics["unresolved"] == 693
    assert "terminal_metrics" in metrics, (
        "the loose reading must stay available beside the authoritative one"
    )
    assert metrics["step_localization"]["n"] == 443, (
        "localization is scored over the gold-error cases that carry a step"
    )
    assert metrics["step_localization"]["hits"] > 0

    assert sorted(report["by_source"]) == sorted(SOURCES)
    manifest = baseline.load_case_list(
        manifest_path=MANIFEST, dataset_root=DATASET_ROOT,
    )
    for source, block in report["by_source"].items():
        assert block["n"] == manifest["source_counts"][source]
        assert block["metrics"]["decisive"] <= block["n"]
    assert sorted(report["by_gold"]) == ["clean", "error"]
    assert (
        report["by_gold"]["error"]["n"] + report["by_gold"]["clean"]["n"] == 693
    )

    timing = report["timing"]
    assert timing["measured_cases"] == 693
    assert timing["unmeasured_cases"] == 0
    assert timing["p50_seconds"] is not None
    assert timing["p90_seconds"] is not None
    assert timing["max_seconds"] >= timing["p50_seconds"]
    assert len(timing["slowest"]) == 5
    assert [row["elapsed_seconds"] for row in timing["slowest"]] == sorted(
        (row["elapsed_seconds"] for row in timing["slowest"]), reverse=True,
    )

    assert len(report["rows"]) == 693
    assert {row["source"] for row in report["rows"]} == set(SOURCES)
    assert all(row["elapsed_seconds"] is not None for row in report["rows"])

    assert sorted(detector.calls) == sorted(manifest["cases"])
    written = list((tmp_path / "runs" / "dry" / "cases").glob("*/result.json"))
    assert len(written) == 693
    assert (tmp_path / "runs" / "dry" / "run.json").exists()
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))[
        "coverage"
    ]["found_results"] == 693


def test_dry_run_resume_reuses_finished_cases(tmp_path):
    first = _run(tmp_path)
    assert len(_OfflineDetector.instances[-1].calls) == 693
    second = _run(tmp_path)
    assert _OfflineDetector.instances[-1].calls == [], (
        "a resume must not re-run a case whose result is already in the tree"
    )
    assert second["coverage"]["found_results"] == 693
    assert second["metrics"]["decisive"] == first["metrics"]["decisive"]


def test_dry_run_retry_unresolved_replaces_only_undecided_rows(tmp_path):
    """The outage is resumable to full coverage, and only where it hit."""
    _OfflineDetector.mode = "outage"
    first = _run(tmp_path, mode="outage")
    first_detector = _OfflineDetector.instances[-1]
    undecided = first_detector.tally["unresolved"]
    assert undecided > 0
    assert first["coverage"]["provider_unresolved"] == undecided
    assert first["coverage"]["authoritative"] == (
        693 - undecided - first_detector.tally["degraded"]
    )

    second = _run(tmp_path, mode="ok", retry_unresolved=True)
    calls = _OfflineDetector.instances[-1].calls
    assert len(calls) == undecided, (
        "retry_unresolved re-runs exactly the undecided rows and reuses the rest"
    )
    assert second["coverage"]["provider_unresolved"] == 0
    assert second["coverage"]["authoritative"] == (
        second["metrics"]["decisive"]
    )
    assert second["coverage"]["degraded"] > 0


def test_the_transport_guard_this_module_relies_on_is_live(monkeypatch):
    """A guard that never fires is not a guard.

    The dry-run tests above prove the batch ran offline. This one proves the
    thing that would have stopped it is not vacuous: the transport entry points
    refuse a call. The module fixture's recorder is deliberately bypassed here
    (that is what lets this test make calls at all), so the assertions are on
    this test's own patch.
    """
    import detector.provider as provider

    for name in ("provider_call", "_isolated_transport"):
        refused: list[dict] = []

        def _refuse(**kwargs):
            refused.append(kwargs)
            raise AssertionError("the dry run attempted a live provider call")

        monkeypatch.setattr(provider, name, _refuse, raising=False)
        with pytest.raises(AssertionError, match="live provider call"):
            getattr(provider, name)(
                endpoint="https://example.invalid/v1", payload={},
            )
        assert refused, f"{name} did not reach the guard at all"
        monkeypatch.undo()


def test_offline_run_needs_no_credentials_and_does_not_claim_the_real_routes(
    tmp_path, monkeypatch,
):
    """The dry run must not require an API key, nor fingerprint real routes.

    ``run_batch`` falls back to ``load_config()`` when it is given no config,
    and ``load_config`` requires a credentialed environment. An offline run
    that reached that fallback would (a) fail on a machine with no credentials
    and (b) write a manifest claiming the real model was used, which is exactly
    the kind of unverifiable claim the manifest exists to prevent.
    """
    for key in ("AUTODL_API_KEY", "DETECTOR_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    class _Bare(_OfflineDetector):
        def __init__(self, config):
            # Deliberately no dataset_root in the config: the offline path must
            # not depend on the caller supplying routes for a model that will
            # never be called, so the fake looks the corpus up itself.
            super().__init__({"dataset_root": str(DATASET_ROOT)})
            self.received_config = dict(config or {})

    _OfflineDetector.instances.clear()
    report = baseline.run_baseline(
        dataset_root=DATASET_ROOT,
        manifest_path=MANIFEST,
        run_id="dry",
        runs_root=tmp_path / "runs",
        report_path=tmp_path / "report.json",
        workers=4,
        detector_factory=_Bare,
    )
    assert report["coverage"]["found_results"] == 693
    manifest = json.loads(
        (tmp_path / "runs" / "dry" / "run.json").read_text(encoding="utf-8")
    )
    assert manifest["config_hash"], "the manifest must still pin something"
    assert "GLM" not in json.dumps(manifest)
    assert _OfflineDetector.instances[-1].received_config == {
        "offline_dry_run": True,
    }, "the batch must be pinned to the offline stub, not to the real config"


def test_cli_offline_mode_runs_without_credentials(tmp_path, monkeypatch):
    """``--detector-factory`` alone is a complete command line."""
    for key in ("AUTODL_API_KEY", "DETECTOR_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    report_path = tmp_path / "cli_report.json"
    exit_code = baseline.main([
        "--dataset-root", str(DATASET_ROOT),
        "--manifest", str(MANIFEST),
        "--run-id", "cli_dry",
        "--runs-root", str(tmp_path / "runs"),
        "--report", str(report_path),
        "--workers", "4",
        "--detector-factory", "scripts.dryrun_fake_detector:OfflineDetector",
    ])
    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["coverage"]["found_results"] == 693
    # The shipped fake must be importable from the CLI, not only from a test.
    from scripts.dryrun_fake_detector import OfflineDetector

    assert OfflineDetector is not None


def test_token_total_is_flagged_as_a_lower_bound_when_attempts_omit_usage(tmp_path):
    """A spend figure that silently drops failed attempts is not the spend.

    Provider attempts that die in transport report no counters, so their tokens
    never reach the sum. Reporting the sum alone invites reading it as the
    cost; the report has to say which attempts were unaccounted for, or a run
    with more outages looks cheaper than one with fewer.
    """
    report = _run(tmp_path)
    usage = report["usage"]
    # The offline fake reports no provider usage at all, which is the extreme
    # case of this gap: the total exists, and it is a floor over a stated count.
    assert "attempts_without_usage" in usage
    assert "total_is_lower_bound" in usage
    if usage["attempts_without_usage"] or usage["usage_unknown_cases"]:
        assert usage["total_is_lower_bound"] is True
        assert "lower bound" in usage["scope"] or "at or above" in usage["scope"]
    else:
        assert usage["total_is_lower_bound"] is False


def test_usage_lower_bound_counts_attempts_that_reported_nothing(tmp_path, monkeypatch):
    """The count comes from ``execution.usage_missing_attempts``, per case.

    Written against a hand-built run tree rather than the fake: the fake takes
    the branch above, and a field that only ever reads zero would pass while
    measuring nothing. The execution carries its request observations, so the
    source attribution itself is verifiable: 20 attempts, 12 of which reported
    no usage, with totals summed from the 8 that did. A hand-built execution
    *without* that lineage reads as an unknown source, which the report then
    refuses to certify as a lower bound -- both halves are asserted below.
    """
    from detector.runner import run_batch, record_case
    from detector.dataset import load_case
    from detector.interventions import build_intervention_registry
    data = tmp_path / "data"
    case = "BFCL/000.json"
    path = data / case
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"question": "q", "history": [{"content": "a"}], "is_hallucination": True}), encoding="utf-8")
    class FakeDetector:
        def __init__(self, config):
            self.config = config
        def detect(self, record, *, case_id, **kwargs):
            return _envelope(case_id, state="verified_error", authority="authoritative")
    monkeypatch.setattr("detector.runner.Detector", FakeDetector)
    run_batch(runs_root=tmp_path / "runs", run_id="gap", dataset_root=data, cases=[case],
              config={}, interventions=build_intervention_registry([]), workers=1)
    result_path = tmp_path / "runs" / "gap" / "cases" / safe_case_id(case) / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.pop("case_commit")
    reported = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    requests = [
        {"purpose": f"attempt_{index}", "usage": dict(reported)}
        for index in range(8)
    ] + [
        {"purpose": f"attempt_{index}", "usage": None}
        for index in range(8, 20)
    ]
    result.update({
        "case_id": "BFCL/000.json",
        "state": "verified_error",
        "execution": {
            "provider_attempts": 20,
            "usage_missing_attempts": 12,
            "requests": requests,
            "usage": {
                "prompt_tokens": 80, "completion_tokens": 40,
                "total_tokens": 120,
            },
        },
        "providers": {
            "judge": {"usage_total": {
                "prompt_tokens": 80, "completion_tokens": 40,
                "total_tokens": 120,
            }},
        },
    })
    record_case(runs_root=tmp_path / "runs", run_id="gap", case_id=case,
                input_record=load_case(data, case)[0], result=result, elapsed_seconds=1)
    report = baseline.usage_report(
        runs_root=tmp_path / "runs", run_id="gap", cases=["BFCL/000.json"],
    )
    assert report["attempts_without_usage"] == 12
    assert report["cases_with_missing_usage"] == 1
    assert report["usage_source"] == "request_observations"
    assert report["total_is_lower_bound"] is True
    assert report["total_tokens"] == 120

    # A hand-built execution with no observation lineage is not a source the
    # report may certify: the totals stay, the lower-bound claim does not.
    run_batch(runs_root=tmp_path / "runs", run_id="gap2", dataset_root=data,
              cases=[case], config={},
              interventions=build_intervention_registry([]), workers=1)
    unverifiable_path = (
        tmp_path / "runs" / "gap2" / "cases" / safe_case_id(case) / "result.json"
    )
    unverifiable = json.loads(unverifiable_path.read_text(encoding="utf-8"))
    unverifiable.pop("case_commit")
    unverifiable.update({
        "case_id": "BFCL/000.json",
        "state": "verified_error",
        "execution": {
            "provider_attempts": 20,
            "usage_missing_attempts": 12,
            "usage": {
                "prompt_tokens": 80, "completion_tokens": 40,
                "total_tokens": 120,
            },
        },
    })
    record_case(runs_root=tmp_path / "runs", run_id="gap2", case_id=case,
                input_record=load_case(data, case)[0], result=unverifiable,
                elapsed_seconds=1)
    unknown = baseline.usage_report(
        runs_root=tmp_path / "runs", run_id="gap2", cases=["BFCL/000.json"],
    )
    assert unknown["usage_source"] == "unknown"
    assert unknown["total_is_lower_bound"] is None


def test_dry_run_reports_undecided_rows_by_cause(tmp_path):
    """``unresolved`` in the metrics is not the same as "no answer at all".

    ``binary_metrics`` defines unresolved as ``decisive``'s complement, so a
    degraded judge-only read is counted there even though the detector did
    answer. The report must decompose that, or a run with degraded rows and no
    provider failures reads as if every case had failed.
    """
    report = _run(tmp_path)
    decomposition = report["coverage"]["undecided_decomposition"]
    assert decomposition["metrics_unresolved"] == report["metrics"]["unresolved"]
    assert decomposition["degraded_rows_with_a_non_authoritative_answer"] == (
        report["coverage"]["degraded"]
    )
    assert decomposition["rows_with_no_answer_at_all"] == (
        report["coverage"]["no_terminal_decision"]
    )
    assert decomposition["metrics_unresolved"] == (
        decomposition["degraded_rows_with_a_non_authoritative_answer"]
        + decomposition["rows_with_no_answer_at_all"]
    )


def test_baseline_refuses_a_case_list_that_does_not_reconcile(tmp_path):
    """A manifest naming a case the corpus lacks is a different measurement."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"cases": ["BFCL/000.json", "Nope/999.json"]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not reconcile"):
        baseline.run_baseline(
            dataset_root=DATASET_ROOT,
            manifest_path=manifest,
            run_id="dry",
            runs_root=tmp_path / "runs",
            config={"dry_run": True, "dataset_root": str(DATASET_ROOT)},
            detector_factory=_OfflineDetector,
        )


def test_baseline_reports_a_missing_result_as_missing_not_as_clean(tmp_path):
    """An unanswered case is missing, never a clean verdict.

    This is the accounting that a full-corpus number stands on: if a case that
    never ran could be read as ``verified_clean``, coverage and accuracy would
    both improve the more cases failed.
    """
    report = _run(tmp_path)
    victim = report["rows"][0]["case"]
    path = (
        tmp_path / "runs" / "dry" / "cases" / safe_case_id(victim) / "result.json"
    )
    path.unlink()
    rescored = baseline.build_report(
        dataset_root=DATASET_ROOT,
        runs_root=tmp_path / "runs",
        run_id="dry",
        cases=[row["case"] for row in report["rows"]],
        case_list=report["case_list"],
        batch_summary=None,
    )
    assert rescored["coverage"]["missing_cases"] == [victim]
    assert rescored["coverage"]["found_results"] == 692
    assert rescored["coverage"]["provider_unresolved"] == 1
    assert rescored["metrics"]["decisive"] == report["metrics"]["decisive"] - 1
    missing_row = next(
        row for row in rescored["rows"] if row["case"] == victim
    )
    assert missing_row["state"] == "missing_result"
    assert missing_row["prediction"] is None
