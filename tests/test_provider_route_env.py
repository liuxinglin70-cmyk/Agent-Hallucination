from __future__ import annotations

import os


def test_dotenv_loader_does_not_override_process_environment(tmp_path, monkeypatch):
    from src.sehd_v6.env_loader import load_local_env

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nFROM_FILE=loaded\nexport QUOTED=\"value\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FROM_FILE", "process_wins")
    monkeypatch.delenv("QUOTED", raising=False)

    loaded = load_local_env(env_file)

    assert loaded == env_file
    assert os.environ["FROM_FILE"] == "process_wins"
    assert os.environ["QUOTED"] == "value"


def test_evolution_mutation_route_defaults_to_official_and_supports_overrides(monkeypatch):
    from src.sehd_v6.evolution.runner import default_mutation_route

    for key in (
        "SEHD_MUTATION_ENDPOINT",
        "SEHD_MUTATION_MODEL",
        "SEHD_MUTATION_API_KEY_ENV",
        "SEHD_MUTATION_TEMPERATURE",
        "SEHD_MUTATION_MAX_TOKENS",
    ):
        monkeypatch.delenv(key, raising=False)
    default = default_mutation_route()
    assert default["endpoint"] == "https://api.deepseek.com"
    assert default["model"] == "deepseek-v4-flash"
    assert default["api_key_environment"] == "DEEPSEEK_API_KEY"

    monkeypatch.setenv("SEHD_MUTATION_ENDPOINT", "https://proxy.example/v1")
    monkeypatch.setenv("SEHD_MUTATION_MODEL", "DeepSeek-V4-Pro")
    monkeypatch.setenv("SEHD_MUTATION_API_KEY_ENV", "CUSTOM_KEY")
    monkeypatch.setenv("SEHD_MUTATION_TEMPERATURE", "0.1")
    monkeypatch.setenv("SEHD_MUTATION_MAX_TOKENS", "321")
    override = default_mutation_route()
    assert override == {
        "endpoint": "https://proxy.example/v1",
        "model": "DeepSeek-V4-Pro",
        "api_key_environment": "CUSTOM_KEY",
        "temperature": 0.1,
        "max_tokens": 321,
    }


def test_model_aliases_canonicalize_to_official_wire_names():
    from src.sehd_v6.core.provider_models import canonical_model_name

    assert canonical_model_name("DeepSeek-V4-Flash") == "deepseek-v4-flash"
    assert canonical_model_name("deepseek-v4-flash") == "deepseek-v4-flash"
    assert canonical_model_name("DeepSeek-V4-Pro") == "deepseek-v4-pro"
    assert canonical_model_name("deepseek-v4-pro") == "deepseek-v4-pro"


def test_official_v4_mutation_defaults_to_non_thinking_json_mode():
    from src.sehd_v6.provider_routes import provider_thinking_control

    route = {
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    }
    assert provider_thinking_control(route, purpose="mutation") == {
        "type": "disabled"
    }
    assert provider_thinking_control(route, purpose="detector") is None
    assert provider_thinking_control(
        {**route, "thinking": "enabled"}, purpose="mutation"
    ) == {"type": "enabled"}


def test_official_detector_config_bounds_strict_judge_json_routes():
    from pathlib import Path

    from src.sehd_v6.core.paca_tcl.detector import load_detector_config

    config = load_detector_config(
        Path("src/sehd_v6/configs/paca_tcl_detector_v3_5.json"),
        require_credentials=False,
    )
    # Strict judge JSON routes disable provider-side thinking so reasoning does
    # not consume the entire output budget and leave the contract body empty.
    # Other detector routes retain model reasoning defaults for detection
    # quality; only appeal routes carry an explicit generation/time budget.
    appeal_routes_with_budget = {
        "appeal_prosecutor_route", "appeal_defender_route",
    }
    bounded_judge_routes = appeal_routes_with_budget | {"boundary_route"}
    for name, route in config.items():
        if not name.endswith("_route"):
            continue
        if name in bounded_judge_routes:
            assert route.get("thinking") == {"type": "disabled"}, name
        else:
            assert "thinking" not in route, name
        if name in appeal_routes_with_budget:
            assert route.get("max_tokens") == 8192, name
            assert route.get("transport_retries") == 3, name
            assert route.get("timeout_seconds") == 180, name
            assert route.get("total_timeout_seconds") == 240, name
        else:
            assert "max_tokens" not in route, name


def test_unified_route_resolution_precedence_and_official_defaults(monkeypatch):
    from src.sehd_v6.provider_routes import resolve_provider_route

    route = resolve_provider_route(
        {
            "endpoint": "https://legacy.invalid",
            "model": "DeepSeek-V4-Flash",
            "api_key_environment": "LEGACY_KEY",
        },
        purpose="detector",
        route_name="defense_route",
        environ={
            "SEHD_ROUTE_DEFENSE_ENDPOINT": "https://route.example",
            "SEHD_DETECTOR_MODEL": "deepseek-v4-pro",
            "SEHD_PROVIDER_API_KEY_ENV": "GLOBAL_KEY",
        },
    )
    assert route["endpoint"] == "https://route.example"
    assert route["model"] == "deepseek-v4-pro"
    assert route["api_key_environment"] == "GLOBAL_KEY"

    default = resolve_provider_route({}, purpose="mutation", environ={})
    assert default["endpoint"] == "https://api.deepseek.com"
    assert default["model"] == "deepseek-v4-flash"
    assert default["api_key_environment"] == "DEEPSEEK_API_KEY"


def test_missing_provider_credential_fails_closed_without_value(monkeypatch):
    from src.sehd_v6.provider_routes import resolve_provider_credential

    monkeypatch.delenv("MISSING_PROVIDER_KEY", raising=False)
    try:
        resolve_provider_credential(
            {"api_key_environment": "MISSING_PROVIDER_KEY"},
            purpose="judge",
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "MISSING_PROVIDER_KEY" in message
        assert "sk-" not in message
    else:  # pragma: no cover
        raise AssertionError("missing provider credential did not fail closed")


def test_deepseek_key_never_replaces_nonofficial_proxy_selector():
    from src.sehd_v6.provider_routes import resolve_provider_route

    route = resolve_provider_route(
        {
            "endpoint": "https://proxy.example/v1",
            "model": "DeepSeek-V4-Flash",
            "api_key_environment": "AUTODL_API_KEY",
        },
        purpose="detector",
        environ={"DEEPSEEK_API_KEY": "test-only-placeholder"},
    )
    assert route["api_key_environment"] == "AUTODL_API_KEY"


def test_proxy_route_without_key_selector_fails_closed():
    from src.sehd_v6.provider_routes import resolve_provider_credential, resolve_provider_route

    route = resolve_provider_route(
        {"endpoint": "https://proxy.example/v1"},
        purpose="detector",
        environ={"DEEPSEEK_API_KEY": "test-only-placeholder"},
    )
    assert route.get("api_key_environment", "") == ""
    try:
        resolve_provider_credential(route, purpose="detector")
    except RuntimeError as exc:
        assert "api-key environment" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("proxy route unexpectedly inherited DeepSeek key")


def test_config_route_purpose_is_not_overwritten_at_transport_boundary(
    tmp_path, monkeypatch,
):
    import json

    from src.sehd_v6.core.paca_tcl import basic_runner
    from src.sehd_v6.provider_routes import resolve_config_provider_routes

    env = {
        "SEHD_JUDGE_ENDPOINT": "https://judge.example",
        "SEHD_DETECTOR_ENDPOINT": "https://detector.example",
        "SEHD_JUDGE_MODEL": "deepseek-v4-pro",
        "SEHD_JUDGE_API_KEY_ENV": "JUDGE_KEY",
        "JUDGE_KEY": "test-only-placeholder",
    }
    config = resolve_config_provider_routes(
        {
            "appeal_prosecutor_route": {
                "endpoint": "https://legacy.example",
                "model": "DeepSeek-V4-Flash",
                "api_key_environment": "LEGACY_KEY",
            }
        },
        environ=env,
    )
    route = config["appeal_prosecutor_route"]
    assert route["endpoint"] == "https://judge.example"
    assert route["_provider_route_resolved"] is True

    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "{}"}}], "usage": {}}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setenv("JUDGE_KEY", "test-only-placeholder")
    monkeypatch.setattr(basic_runner.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        basic_runner,
        "_global_cache_path",
        lambda request_hash: tmp_path / f"{request_hash}.json",
    )
    basic_runner.provider_call_with_schema(
        route=route,
        system="return json",
        user="{}",
        cache_dir=tmp_path,
        require_candidates=False,
    )
    assert str(captured["url"]).startswith("https://judge.example/")
    assert captured["payload"]["model"] == "deepseek-v4-pro"


def test_resolved_judge_route_uses_judge_thinking_control(tmp_path, monkeypatch):
    import json

    from src.sehd_v6.core.paca_tcl import basic_runner

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "{}"}}], "usage": {}}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(basic_runner.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(basic_runner, "_global_cache_path", lambda _hash: None)
    basic_runner.provider_call_with_schema(
        route={
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key_environment": "DEEPSEEK_API_KEY",
            "thinking": {"type": "disabled"},
            "_provider_purpose": "judge",
            "_provider_route_resolved": True,
            "transport_retries": 0,
        },
        system="return json", user="{}", cache_dir=tmp_path,
        require_candidates=False,
    )
    assert captured["payload"]["thinking"] == {"type": "disabled"}


def test_provider_transport_retries_429_using_bounded_retry_after(
    tmp_path, monkeypatch,
):
    import io
    import json
    from urllib import error as urlerror

    from src.sehd_v6.core.paca_tcl import basic_runner

    attempts = 0
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "{}"}}], "usage": {}}
            ).encode()

    def fake_urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urlerror.HTTPError(
                request.full_url, 429, "Too Many Requests",
                {"Retry-After": "12"}, io.BytesIO(),
            )
        return Response()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(basic_runner.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(basic_runner.time, "sleep", sleeps.append)
    monkeypatch.setattr(basic_runner, "_global_cache_path", lambda _hash: None)
    value, _meta = basic_runner.provider_call_with_schema(
        route={
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key_environment": "DEEPSEEK_API_KEY",
            "transport_retries": 1,
            "total_timeout_seconds": 60,
        },
        system="return json", user="{}", cache_dir=tmp_path,
        require_candidates=False,
    )
    assert value == {}
    assert attempts == 2
    assert sleeps == [12.0]


def test_provider_transport_honors_route_generation_budget_and_total_timeout(
    tmp_path, monkeypatch,
):
    import json

    from src.sehd_v6.core.paca_tcl import basic_runner

    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "{}"}}], "usage": {}}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(basic_runner.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        basic_runner,
        "_global_cache_path",
        lambda request_hash: tmp_path / f"{request_hash}.json",
    )
    basic_runner.provider_call_with_schema(
        route={
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key_environment": "DEEPSEEK_API_KEY",
            "temperature": 0.3,
            "max_tokens": 77,
            "timeout_seconds": 20,
            "total_timeout_seconds": 4,
            "transport_retries": 0,
        },
        system="return json",
        user="{}",
        cache_dir=tmp_path,
        require_candidates=False,
        purpose="mutation",
    )
    assert captured["payload"]["temperature"] == 0.3
    assert captured["payload"]["max_tokens"] == 77
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["timeout"] == 4


def test_lowercase_pro_route_omits_unsupported_json_response_format(tmp_path, monkeypatch):
    import json

    from src.sehd_v6.core.paca_tcl import proof_evaluator

    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "{}"}}], "usage": {}}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(proof_evaluator.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        proof_evaluator,
        "_global_cache_path",
        lambda request_hash: tmp_path / f"{request_hash}.json",
    )

    value, _meta = proof_evaluator._json_object_provider_call(
        route={
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "api_key_environment": "DEEPSEEK_API_KEY",
            "transport_retries": 0,
            "timeout_seconds": 5,
        },
        system="return json",
        user="{}",
        cache_dir=tmp_path,
    )

    assert value == {}
    assert "response_format" not in captured["payload"]


def test_detector_transport_omits_pro_response_format(tmp_path, monkeypatch):
    import json

    from src.sehd_v6.core.paca_tcl import basic_runner

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "{}"}}], "usage": {},
            }).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(basic_runner.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(basic_runner, "_global_cache_path", lambda _hash: None)
    basic_runner.provider_call_with_schema(
        route={
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "api_key_environment": "DEEPSEEK_API_KEY",
            "transport_retries": 0,
        },
        system="return json", user="{}", cache_dir=tmp_path,
        require_candidates=False,
    )
    assert "response_format" not in captured["payload"]


def test_evolution_accepts_case_insensitive_route_alias_without_provider_call(
    tmp_path, monkeypatch,
):
    from src.sehd_v6.core.bccse import evolution_runner as runner

    monkeypatch.setattr(
        runner,
        "_run_detection_with_genome",
        lambda **kwargs: {
            "state": "verified_clean",
            "runtime_valid": True,
            "decision_authority": "valid",
            "census": [],
        },
    )
    # Keep this validator test offline; the route must be accepted without
    # allowing a real mutation request to escape the test process.
    monkeypatch.setattr(
        runner,
        "json_object_provider_call",
        lambda **kwargs: ({"operation": "no_edit", "reason": "test"}, {}),
    )

    result = runner.run_online_evolution(
        evolution_case_ids=("stable",),
        records={"stable": {}},
        config={},
        output_dir=tmp_path,
        census_root=tmp_path,
        mutation_route={
            "endpoint": "https://api.deepseek.com",
            "model": "DeepSeek-V4-Flash",
            "api_key_environment": "DEEPSEEK_API_KEY",
        },
        run_id="route_alias",
    )

    assert result.edits_accepted == 0
    assert result.steps[0].gate_decision in {"skip_stable", "skip_no_edit"}
