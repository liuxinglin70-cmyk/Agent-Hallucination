"""The test suite must not reach a live provider. Enforced, not assumed.

A test that calls a model service is three problems at once: it costs money on
every run, its result depends on a service that may be down, and it violates
this project's standing rule that real-service runs happen only when they are
explicitly approved. The failure mode is quiet -- a mock *backend* is not a mock
*proposer*, and ``preview_cycle`` drafts proposals with whatever proposer the
configuration names -- so it is guarded here rather than left to reviewer
attention.

The guard patches the real network entry points that the framework uses, so a
mistake raises at the moment of the call with the reason, instead of being
discovered later in a provider report.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_live_provider(monkeypatch, request):
    """Refuse any HTTP exchange from inside the test suite.

    Opt out with ``@pytest.mark.allow_live_provider``. Nothing in this
    repository uses it: a test that genuinely needs the service is a run, not a
    test, and it belongs in ``reports/`` with its own script and its own record.
    """
    if request.node.get_closest_marker("allow_live_provider"):
        yield
        return

    message = (
        "the test suite attempted a live provider call. A run against the real "
        "service is not a test: it needs explicit approval and its own record. "
        "Set proposer.mode='offline' (or inject a scripted proposer) so the "
        "drafting step is deterministic and free."
    )

    def _refuse(*args, **kwargs):
        raise AssertionError(message)

    def _refuse_transport(**kwargs):
        raise AssertionError(message)

    # The two layers a provider call can go through: the detector's HTTP
    # exchange (which runs in a child process) and the proposer's call site.
    import detector.provider as provider

    monkeypatch.setattr(provider, "_isolated_transport", _refuse_transport, raising=False)
    for name in ("provider_call", "_execute_transport"):
        if hasattr(provider, name):
            monkeypatch.setattr(provider, name, _refuse, raising=False)

    # ``urllib.request.urlopen`` is what the child process would use; patching
    # it in-process covers any call path that does not go through the isolated
    # transport.
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _refuse, raising=False)
    yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_live_provider: permit a live provider call in this test "
        "(unused; a real run belongs in reports/, not in the suite)",
    )
