import json

from evolution.metrics import fired_rate
from evolution.worksheet import worksheet_report


def test_unmatched_predicate_is_not_runtime_action():
    def row(event):
        return {"result": {"proof": {"deployment": {"fired": [
            {"event": event, "intervention_id": "x"}]}}}}
    assert fired_rate([row("recall_trigger_not_matched")], "x") == 0
    assert fired_rate([row("recall_triggered")], "x") == 1


def test_unresolved_is_neither_false_negative_nor_false_positive():
    rows = [
        {"case": "a", "gold": True, "prediction": None},
        {"case": "b", "gold": False, "prediction": None},
        {"case": "c", "gold": True, "prediction": False},
    ]
    report = worksheet_report(rows, [])
    assert (report["fn"], report["fp"], report["unresolved"]) == (1, 0, 2)


def test_cross_cycle_cache_retries_outage_but_caches_authoritative_result(tmp_path):
    from evolution.backends import CachingBackend
    class Backend:
        name = "outage_test"
        calls = 0
        def run_case(self, record, *, case_id, **kwargs):
            self.calls += 1
            return {"case_id": case_id, "decision_authority":
                    "unresolved" if self.calls == 1 else "authoritative"}
    backend = Backend()
    cache = CachingBackend(backend, tmp_path)
    record = {"question": "q", "history": [{"role": "assistant", "content": "answer"}]}
    assert cache.run_case(record, case_id="a")["decision_authority"] == "unresolved"
    assert cache.run_case(record, case_id="a")["decision_authority"] == "authoritative"
    assert cache.run_case(record, case_id="a")["decision_authority"] == "authoritative"
    assert backend.calls == 2


def test_declared_but_unreadable_mechanism_binding_is_not_a_silent_fallback(
    tmp_path,
):
    """A run that asked to be scored against a mechanism must not read as unbound.

    The binding loader leaves both dicts empty when the file cannot be read, so
    checking "empty means unbound" before checking the recorded error reported
    an unreadable binding as a run that never asked to be bound -- and the
    report then carried no mechanism reading at all.
    """
    from evolution.config import load_config
    from evolution.engine import EvolutionEngine
    from evolution.splits import EvolutionSplit

    config = load_config()
    data = tmp_path / "data"
    case = "A/001.json"
    (data / "A").mkdir(parents=True)
    (data / "A" / "001.json").write_text(
        json.dumps({"question": "q", "history": [], "is_hallucination": True,
                    "hallucination_step": 1}),
        encoding="utf-8",
    )
    config["dataset_root"] = str(data)
    config["work_root"] = str(tmp_path / "work")
    config["registry_dir"] = str(tmp_path / "registry")
    config["ledger_path"] = str(tmp_path / "ledger.jsonl")
    config["harness_dir"] = str(tmp_path / "harness")
    config["memory_root"] = str(tmp_path / "memory")
    config["mechanism"] = str(tmp_path / "missing-mechanism.json")
    config["mechanism_membership"] = str(tmp_path / "missing-membership.json")
    split = EvolutionSplit(
        name="synthetic", pool=[case], sentinel=[case], gain=[case],
        patrol_pool=[], mini=True,
    )
    engine = EvolutionEngine(config, split=split, mini=True)
    assert engine._mechanism_binding_error
    reading = engine._mechanism_opportunity([{"case": "A/001.json", "gold": True}])
    assert reading is not None
    assert reading["evaluable"] is False
    assert reading["reason_code"] == "mechanism_membership_invalid"
    # And the declared-but-invalid reading has a category, not "unclassified".
    from evolution.promotion_alignment import diagnosis
    assert diagnosis(reading["reason_code"])["category"] != "unclassified"


def test_freezing_twice_without_a_ledger_still_guards_one_path(tmp_path):
    """The per-path frozen file is the fallback guard when no ledger is given."""
    import json

    import pytest as _pytest

    from evolution.promotion import PromotionProtocolError, freeze_protocol

    roles = {"A/001.json": "benefit", "A/002.json": "benefit",
             "C/001.json": "protection", "C/002.json": "protection",
             "E/001.json": "protection", "E/002.json": "protection"}
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(sorted(roles)), encoding="utf-8")
    detector_config = tmp_path / "detector.json"
    detector_config.write_text(
        json.dumps({"schema_version": "detector.config.v1"}), encoding="utf-8",
    )
    gold = {"A/001.json": True, "A/002.json": False, "C/001.json": True,
            "C/002.json": True, "E/001.json": False, "E/002.json": False}
    kwargs = dict(batch_path=batch, roles=roles, gold=gold,
                  detector_config=detector_config, workers=2,
                  framework_config={"registry_dir": str(tmp_path / "registry")})
    freeze_protocol(tmp_path / "protocol.json", **kwargs)
    with _pytest.raises(PromotionProtocolError) as exc:
        freeze_protocol(tmp_path / "protocol.json", **kwargs)
    assert "already frozen" in str(exc.value)


def test_cli_refuses_a_case_declared_in_two_role_sets(tmp_path):
    """§8.2's three sets are disjoint; a case in two of them is a contradiction.

    The flag lists were merged as one dict, so the last flag silently won and
    the batch was frozen with a different meaning than the one declared.
    """
    import pytest as _pytest

    from evolution.cli import build_parser, cmd_freeze_protocol

    data = tmp_path / "data"
    cases = ["A/001.json", "A/002.json", "C/001.json", "C/002.json",
             "E/001.json", "E/002.json"]
    for index, case in enumerate(cases):
        path = data / case
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "question": f"q{index}", "history": [],
            "is_hallucination": index % 2 == 0,
        }), encoding="utf-8")
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(cases), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "schema_version": "evolution.framework.v1",
        "dataset_root": str(data),
        "work_root": str(tmp_path / "work"),
        "registry_dir": str(tmp_path / "registry"),
        "ledger_path": str(tmp_path / "ledger.jsonl"),
        "harness_dir": str(tmp_path / "harness"),
        "memory_root": str(tmp_path / "memory"),
    }), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args([
        "freeze-protocol",
        "--batch", str(batch),
        "--protocol-out", str(tmp_path / "protocol.json"),
        "--benefit", "A/001.json,A/002.json",
        # The same case under two roles: a contradiction, not a precedence.
        "--protection", "A/001.json,C/001.json,C/002.json",
        "--challenge", "E/001.json,E/002.json",
        "--config", str(config),
    ])
    with _pytest.raises(SystemExit) as exc:
        cmd_freeze_protocol(args)
    assert "exactly one role" in str(exc.value)
    assert not (tmp_path / "protocol.json").exists()


def test_cli_installs_the_mechanism_binding_and_refuses_half_of_it():
    """The engine-side mechanism layer needs a reachable entry point.

    ``_load_mechanism_binding`` reads ``config['mechanism']`` and
    ``config['mechanism_membership']``, but no shipped config set them, so the
    mechanism-aligned reading was unreachable outside unit tests. The binding
    is evaluator evidence, so both halves are required and a half-declared
    binding is refused here rather than silently degrading to the unbound
    polarity reading.
    """
    import pytest as _pytest

    from evolution.cli import _apply_mechanism_binding, build_parser

    parser = build_parser()
    args = parser.parse_args([
        "cycle", "--cycle", "1",
        "--mechanism-binding", "mechanism.json",
        "--membership-binding", "membership.json",
    ])
    config = _apply_mechanism_binding({}, args)
    assert config["mechanism"] == "mechanism.json"
    assert config["mechanism_membership"] == "membership.json"

    half = parser.parse_args(["cycle", "--cycle", "1", "--membership-binding",
                              "membership.json"])
    with _pytest.raises(SystemExit) as exc:
        _apply_mechanism_binding({}, half)
    assert "both" in str(exc.value)
    # No binding declared at all is legal: the unbound polarity path is intact.
    plain = parser.parse_args(["cycle", "--cycle", "1"])
    assert _apply_mechanism_binding({}, plain) == {}
