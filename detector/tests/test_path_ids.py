import json

import pytest

from detector.dataset import load_case
from detector.runner import run_batch, safe_case_id
from detector.runtime import contained_path, validate_path_id


@pytest.mark.parametrize("case", ["../outside.json", "/outside.json", "C:/outside.json", "C:outside.json",
                                     "A\\file.json", "//server/share/file", "A//file", "A/./file", "A/../file",
                                     "A/NUL.json", "CON", "A/file. ", "A/file:", "", "A/\x00file"])
def test_invalid_case_id_is_rejected_before_read_or_provider(tmp_path, monkeypatch, case):
    def unexpected(*args, **kwargs):
        raise AssertionError("unsafe ids must be rejected before constructing a detector")
    monkeypatch.setattr("detector.runner.Detector", unexpected)
    with pytest.raises(ValueError):
        safe_case_id(case)
    with pytest.raises(ValueError):
        load_case(tmp_path, case)
    with pytest.raises(ValueError):
        run_batch(dataset_root=tmp_path, cases=[case], runs_root=tmp_path / "runs", run_id="r", config={})
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("run_id", ["../r", "A/r", "C:/r", "NUL", "r. "])
def test_run_id_is_validated_before_detector_or_directory(tmp_path, monkeypatch, run_id):
    monkeypatch.setattr("detector.runner.Detector", lambda *a, **k: pytest.fail("constructed before id validation"))
    with pytest.raises(ValueError):
        run_batch(dataset_root=tmp_path, cases=[], runs_root=tmp_path / "runs", run_id=run_id, config={})
    assert not (tmp_path / "runs").exists()


def test_casefold_collisions_are_refused_before_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("detector.runner.Detector", lambda *a, **k: pytest.fail("constructed before collision validation"))
    with pytest.raises(ValueError, match="collide"):
        run_batch(dataset_root=tmp_path, cases=["A/001.json", "a/001.json"], runs_root=tmp_path / "runs", run_id="r", config={})


def test_valid_relative_path_preserves_label_firewall(tmp_path):
    path = tmp_path / "A" / "001.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"question": "q", "history": [{"role": "assistant", "content": "answer"}],
                                "is_hallucination": True}), encoding="utf-8")
    record, gold = load_case(tmp_path, "A/001.json")
    assert "is_hallucination" not in record
    assert gold["is_hallucination"] is True
    assert contained_path(tmp_path, "A/001.json") == path.resolve()


@pytest.mark.platform
def test_directory_link_cannot_escape_declared_root(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(ValueError, match="escapes"):
        contained_path(root, "link/file.json")



@pytest.mark.parametrize("alias", ["COM¹.json", "COM².log", "LPT³.txt", "CON .json", "A/NUL .txt"])
def test_windows_device_alias_is_rejected_without_device_io(tmp_path, alias):
    with pytest.raises(ValueError, match="unsafe"):
        validate_path_id(alias)
    with pytest.raises(ValueError, match="unsafe"):
        contained_path(tmp_path, alias)
