from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from evolution.contracts import save_json


def test_atomic_save_handles_long_cache_paths_concurrently(tmp_path):
    cache = tmp_path / ("namespace-" + "a" * 20) / ("registry-" + "b" * 20)
    paths = [
        cache / f"OpenDeepSearch__{index:03d}.json.{'c' * 12}.json"
        for index in range(20)
    ]

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [
            pool.submit(save_json, path, {"case": index})
            for index, path in enumerate(paths)
        ]
        for future in futures:
            future.result()

    assert [json.loads(path.read_text(encoding="utf-8"))["case"] for path in paths] == list(range(20))


def test_cache_path_degrades_to_digest_past_the_windows_ceiling(tmp_path):
    """A long evidence tag must not push the file path past the ceiling.

    Observed live: one cache directory held nine ``.noevidence`` names that
    fitted and one ``.ev-<digest>`` name that reached 268 characters, so
    ``save_json``'s ``os.replace`` raised WinError 3 and the case
    fail-closed to ``unresolved``. This reproduces the geometry — the
    directory fits, the file name does not — and requires the degraded name
    to stay inside the budget while a short path keeps its descriptive name.
    """
    from evolution.backends import CachingBackend, _CACHE_PATH_MAX, _CACHE_TEMP_NAME

    class Stub:
        name = "stub"
        evidence_provider = None

        def run_case(self, record, *, case_id, registry=None, gold=None):
            return {"case_id": case_id, "decision_authority": "authoritative"}

    registry = {"content_hash": "a" * 64}
    record = {"question": "q", "history": []}
    long_tag = "ev-" + "9" * 44

    # The real overflow: a snapshot tag on a case under a nested work root.
    nested = tmp_path / "artifacts" / "evolution_run"
    backend = CachingBackend(
        Stub(), nested, namespace="detector-stage-replay-bound-hearings",
        extra_key=lambda case_id: long_tag,
    )
    directory = backend.cache_dir / registry["content_hash"]
    path = backend._path("OpenDeepSearch/080.json", registry, record)
    # The fixture must reproduce the geometry: the descriptive name would
    # overflow while the directory itself fits with room for the temp file.
    descriptive = len(str(directory)) + 1 + len(
        "OpenDeepSearch__080.json." + "9" * 12 + "." + long_tag + ".json"
    )
    assert descriptive > _CACHE_PATH_MAX, descriptive
    assert len(str(directory)) + 1 + len(_CACHE_TEMP_NAME) <= 260, "fixture must fit"
    assert len(str(path)) <= _CACHE_PATH_MAX, len(str(path))
    assert path.name.endswith(".json")
    assert path.parent == directory

    # Stable across calls and distinct per case.
    assert backend._path("OpenDeepSearch/080.json", registry, record) == path
    assert backend._path("OpenDeepSearch/081.json", registry, record) != path

    # The file can actually be written and read back at that path.
    save_json(path, {"case_id": "OpenDeepSearch/080.json"})
    assert json.loads(path.read_text(encoding="utf-8"))["case_id"] == "OpenDeepSearch/080.json"

    # A short path keeps the descriptive name, so the fallback stays rare.
    shallow = CachingBackend(
        Stub(), tmp_path / "c", namespace="n",
        extra_key=lambda case_id: long_tag,
    )
    kept = shallow._path("OpenDeepSearch/080.json", registry, record)
    assert kept.name.startswith("OpenDeepSearch__080.json.")
