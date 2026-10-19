"""Bind collected test identities to exactly-once, successful execution."""
from collections import Counter
import json
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--verification-report", help="write the complete offline test execution ledger")


def pytest_configure(config):
    path = config.getoption("--verification-report")
    if path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(ExecutionReport(Path(path)), "verification-execution-report")


class ExecutionReport:
    def __init__(self, path):
        self.path = path
        self.collected = None
        self.executed = []
        self.failed = []
        self.skipped = []
        self.collection_matches = True

    def _collection(self, identifiers):
        identifiers = list(identifiers)
        if self.collected is not None and self.collected != identifiers:
            self.collection_matches = False
        self.collected = identifiers

    def pytest_collection_finish(self, session):
        if not getattr(session.config.option, "numprocesses", 0):
            self._collection(item.nodeid for item in session.items)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        self._collection(ids)

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            self.executed.append(report.nodeid)
        if report.failed:
            self.failed.append({"nodeid": report.nodeid, "phase": report.when})
        if report.skipped:
            self.skipped.append({"nodeid": report.nodeid, "phase": report.when})

    def pytest_sessionfinish(self, session, exitstatus):
        collected, executed = self.collected or [], self.executed
        expected, observed = set(collected), set(executed)
        duplicates = [name for name, count in Counter(executed).items() if count != 1]
        value = {"collected": collected, "executed": executed, "collected_count": len(collected),
                 "executed_count": len(executed), "missing": sorted(expected - observed),
                 "unexpected": sorted(observed - expected), "duplicates": sorted(duplicates),
                 "failed": self.failed, "skipped": self.skipped, "exit_status": int(exitstatus),
                 "worker_collections_match": self.collection_matches}
        value["passed"] = bool(collected) and int(exitstatus) == 0 and self.collection_matches and not any(
            value[key] for key in ("missing", "unexpected", "duplicates", "failed", "skipped"))
        self.path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
