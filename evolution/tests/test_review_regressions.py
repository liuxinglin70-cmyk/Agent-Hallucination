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
    assert cache.run_case({}, case_id="a")["decision_authority"] == "unresolved"
    assert cache.run_case({}, case_id="a")["decision_authority"] == "authoritative"
    assert cache.run_case({}, case_id="a")["decision_authority"] == "authoritative"
    assert backend.calls == 2
