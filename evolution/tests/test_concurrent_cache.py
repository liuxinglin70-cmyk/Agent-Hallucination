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
