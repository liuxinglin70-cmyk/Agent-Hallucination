"""Provider thinking control: the single platform authority that keeps V4
reasoning out of structured-JSON surfaces."""

from __future__ import annotations

from sehd_rise.platform.models import is_deepseek_v4
from sehd_rise.platform.routes import provider_thinking_control


def test_v4_models_default_to_thinking_disabled() -> None:
    assert is_deepseek_v4("deepseek-v4-flash") is True
    assert is_deepseek_v4("DeepSeek-V4-Pro") is True
    assert is_deepseek_v4("other-model") is False
    for model in ("deepseek-v4-flash", "deepseek-v4-pro"):
        assert provider_thinking_control({"model": model}) == {
            "type": "disabled",
        }


def test_explicit_route_setting_is_the_authority() -> None:
    enabled = provider_thinking_control({
        "model": "deepseek-v4-flash",
        "thinking": {"type": "enabled"},
    })
    assert enabled == {"type": "enabled"}
    disabled = provider_thinking_control({
        "model": "deepseek-v4-flash",
        "thinking": {"type": "disabled"},
    })
    assert disabled == {"type": "disabled"}


def test_non_v4_models_pass_through_untouched() -> None:
    assert provider_thinking_control({"model": "some-other-model"}) is None
    assert provider_thinking_control({}) is None


def test_invalid_explicit_mode_fails_closed() -> None:
    import pytest

    with pytest.raises(ValueError):
        provider_thinking_control({
            "model": "deepseek-v4-flash",
            "thinking": {"type": "maybe"},
        })
